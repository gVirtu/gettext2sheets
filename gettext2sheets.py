#!/usr/bin/python3

import argparse
import datetime
import googleapiclient.discovery
import json
import oauth2client.file, oauth2client.client, oauth2client.tools
import os
import re
from enum import Enum
from httplib2 import Http
from pathlib import Path

# ANSI color codes
C_RED = '\033[91m'
C_GREEN = '\033[92m'
C_YELLOW = '\033[93m'
C_BLUE = '\033[94m'
C_PURPLE = '\033[95m'
C_CYAN = '\033[96m'
C_RESET = '\033[0m'

RE_LC_MESSAGES = re.compile(r"/([a-zA-Z-]+)/LC_MESSAGES")
RE_HIGHLIGHT = re.compile(r"__(.*?)__")
RE_ENTRY_FIELD = re.compile(r'^(msg.*)\s"(.*)"$')
RE_EXTRA_STRING = re.compile(r'^\s*"(.*)"\s*$')
RE_ASSIGN = re.compile(r"{([a-zA-Z_-]+)}")
RE_ESCAPED_ASSIGN = re.compile(r"\\{([a-zA-Z_-]+)\\}")

GOOGLE_AUTH_SCOPES = 'https://www.googleapis.com/auth/spreadsheets'
CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'token.json'

CONFIG_FILE = 'config.json'
VERBOSE = False
MODE = None

class Mode(Enum):
    PUSH = 1
    PULL = 2

# Load configuration
with open(CONFIG_FILE) as config:
    CONFIG = json.load(config)

def main():
    handle_args()
    info("Requesting authorization from Google API...")
    service = authorize_google_sheets()
    file_list = find_files(CONFIG["path"], ".po")

    if MODE == Mode.PUSH: return handle_push(service, file_list)
    if MODE == Mode.PULL: return handle_pull(service, file_list)

def handle_args():
    """Handles command-line parameters."""
    global MODE
    global VERBOSE

    parser = argparse.ArgumentParser(description='Sync Gettext messages to Google Sheets.')
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('action', choices=['push', 'pull'])

    args = parser.parse_args()

    MODE = Mode.PULL if args.action == 'pull' else Mode.PUSH
    VERBOSE = args.verbose

def authorize_google_sheets():
    """
    Starts the Google API client using credentials provided by a `credentials.json` file.
    In order to generate this file, visit:
    https://console.developers.google.com/flows/enableapi?apiid=sheets.googleapis.com

    Once authenticated, a `token.json` will be generated for subsequent accesses.
    """
    store = oauth2client.file.Storage(TOKEN_FILE)
    creds = store.get()
    if not creds or creds.invalid:
        flow = oauth2client.client.flow_from_clientsecrets(CREDENTIALS_FILE, GOOGLE_AUTH_SCOPES)
        creds = oauth2client.tools.run_flow(flow, store)

    return googleapiclient.discovery.build('sheets', 'v4', http=creds.authorize(Http()))

def find_files(path, extension):
    """Returns a list of paths to all files that are inside `path` and have the given extension."""
    glob = generate_glob_by_extension(extension)
    return list(Path(path).rglob(glob))

def generate_glob_by_extension(extension):
    """
    Generates a glob that matches the given extension, case-insensitively.

    Example
    -------
    For '.po' files, the generated glob is '*.[pP][oO]'
    """
    extension = extension.lstrip(".")

    case_insensitive_char_list = ["[{0}{1}]".format(char, char.upper()) for char in extension]
    glob = "".join(case_insensitive_char_list)

    return "*.{0}".format(glob)

def handle_push(service, file_list):
    """
    Sends local message data from all .po files to a Google Sheets spreadsheet.
    """
    info("Mode was set to PUSH.")
    locale_row_offsets = {}

    for posix_path in file_list:
        path_string = str(posix_path)
        locale = get_locale_by_path(path_string)
        info("File __{0}__ is from locale __{1}__".format(posix_path, locale))
        file_entries = process_po_file(path_string)
        try:
            settings = CONFIG["locales"][locale]
        except KeyError as e:
            warn("Missing configuration for this locale, skipping...")
            continue

        # Only print header once
        print_header = locale not in locale_row_offsets.keys()
        # Cumulative offset
        row_offset = locale_row_offsets.get(locale, settings["row_offset"])
        # Metadata
        metadata = {"file_name": posix_path.name,
                    "locale": locale,
                    "timestamp": str(datetime.datetime.now())}

        range_str, body = build_request_body(settings, file_entries,
                                                row_offset, print_header, metadata)
        verbose_info("Body: {0}".format(body))
        result = update_sheet(service, CONFIG["spreadsheet_id"], range_str, body)
        verbose_info("Result: {0}".format(str(result)))
        updated_rows = result.get('updatedRows', 0)
        info("Updated {0} rows successfully.".format(updated_rows))
        locale_row_offsets[locale] = row_offset + updated_rows

def get_locale_by_path(path):
    """
    Determines the locale of a .po file based on its path.

    This function assumes all files are within a path of the form '*/<locale>/LC_MESSAGES/*.po'.
    """
    try:
        return RE_LC_MESSAGES.search(path).group(1)
    except Exception:
        error(("Could not determine locale for file {0}. "
               "Make sure it's inside an LC_MESSAGES folder.").format(path))

def process_po_file(path):
    """
    Builds a list of entries from a .po file.
    Each entry will contain keys such as "msgid", "msgstr", "msgid_plural", "msgstr[0]", etc.
    """
    with open(path) as file:
        entry = {}
        entries = []
        info("Reading file __{0}__...".format(path))

        for line in file:
            line = line.strip()
            match = RE_ENTRY_FIELD.match(line)
            if match:
                field = match.group(1)
                value = match.group(2)

                # If this field is already tracked, we finished our last entry
                if field in entry.keys():
                    # Prevent adding the empty msgid from the header
                    if any(entry.values()):
                        entries.append(entry)
                    entry = {}

                entry[field] = value

        # Append last entry
        if entry:
            entries.append(entry)

    info("Read {0} entries!".format(len(entries)))

    return entries

def build_request_body(settings, entries, row_offset, print_header, metadata):
    """
    Generates the body for a request to persist entries to Google Sheets.
    This includes determining the right range for the data, and placing fields into the
    right columns according to the user's configuration.
    """
    sheet = settings["sheet"]
    entry_count = len(entries)
    if not entry_count:
        warn("No entries found in this file, no changes were pushed.")

    row_start = 1 + row_offset
    row_end = row_start + entry_count - 1 + print_header
    columns = settings["columns"]
    column_start = get_column_string(1 + settings["column_offset"])
    column_end = get_column_string(1 + settings["column_offset"] + len(columns) - 1)
    range_name = generate_range_name(sheet, row_start, row_end, column_start, column_end)
    headers = [[column["header"] for column in columns]] if print_header else []

    return (range_name,
            {'values': headers +
                       [build_request_entry(columns, entry, metadata) for entry in entries]})

def build_request_entry(columns, entry, metadata):
    """Processes all columns from a single entry."""
    return [populate_column(column, entry, metadata) for column in columns]

def populate_column(column, entry, metadata):
    """Decides the data that will fill a single column from a specific entry."""
    keys = entry.keys()
    static_text = column.get("static")
    if static_text:
        return parse_static_text(static_text, metadata)
    eligible_fields = column["fields"]
    for field in eligible_fields:
        if field in keys:
            return entry[field]
    return ""

def parse_static_text(static_text, metadata):
    """Replaces a static text's assigns with dynamic metadata."""
    return RE_ASSIGN.sub(lambda match: replace_assign(match, metadata), static_text)

def replace_assign(match_object, metadata):
    return metadata.get(match_object.group(1), "")

def get_column_string(n):
    """Converts index n to a letter-based column index as is used in Google Sheets."""
    chars = []
    a = ord('A')
    while n > 0:
        n, offset = divmod(n-1, 26)
        chars.append(chr(a + offset))
    return "".join(reversed(chars))

def generate_range_name(sheet, row_start, row_end, column_start, column_end):
    return "{0}!{1}{2}:{3}{4}".format(sheet, column_start, row_start, column_end, row_end)

def update_sheet(service, sheet_id, range_string, body):
    """Fires a spreadsheet update request through the Google API client."""
    return service.spreadsheets().values().update(spreadsheetId=sheet_id,
                                                  range=range_string,
                                                  body=body,
                                                  valueInputOption='RAW').execute()

def handle_pull(service, file_list):
    """
    Updates .po files with data fetched from a Google Sheets spreadsheet.
    """
    info("Mode was set to PULL.")

    locale_file_paths = {}

    for posix_path in file_list:
        path_string = str(posix_path)
        locale = get_locale_by_path(path_string)
        info("File __{0}__ is from locale __{1}__".format(posix_path, locale))
        # Path_map associates filenames to full paths
        path_map = locale_file_paths.get(locale, {})
        path_map[posix_path.name] = posix_path
        locale_file_paths[locale] = path_map

    locale_list = locale_file_paths.keys()

    for locale in locale_list:
        try:
            settings = CONFIG["locales"][locale]
        except KeyError as e:
            warn("Missing configuration for locale {0}, skipping...".format(locale))
            continue

        info("Handling pull for locale {0}.".format(locale))
        pull_by_locale(service, locale_file_paths[locale], settings)

def pull_by_locale(service, file_path_map, settings):
    """
    Pulls data from a sheet that corresponds to a single locale.
    """
    sheet = settings["sheet"]
    sheet_id = CONFIG["spreadsheet_id"]
    row_start = 1 + settings["row_offset"] + 1 # Header
    columns = settings["columns"]
    column_start = get_column_string(1 + settings["column_offset"])
    column_end = get_column_string(1 + settings["column_offset"] + len(columns) - 1)
    column_mapping = get_column_mapping(columns)
    chunk_size = CONFIG["pull_chunk_size"]
    file_name_column = column_mapping["_file_name"]
    file_name_template = columns[file_name_column]["static"]
    file_handle_map = {}

    # As we don't know how many rows there are, data is pulled in chunks until there's
    # nothing else to be read.
    while True:
        row_end = row_start + chunk_size - 1
        range_name = generate_range_name(sheet, row_start, row_end, column_start, column_end)
        info("Fetching chunk {0}.".format(range_name))

        count, data = fetch_chunk(service, sheet_id, range_name)
        info("Data: {0}.".format(data))

        if count == 0: break

        process_chunk(file_path_map, file_handle_map, data, column_mapping, file_name_template)

        row_start = row_end + 1

    info("Finished processing. Closing file handles...")

    for file_name, handles in file_handle_map.items():
        handles["write"].close()
        handles["read"].close()
        info("Closed file {0}. Cleaning up...".format(file_name))
        full_path = str(file_path_map[file_name])
        full_path_tmp = "{0}.tmp".format(full_path)
        full_path_old = "{0}.old".format(full_path)
        try:
            os.remove(full_path_old)
            verbose_info("Removed {0}.".format(full_path_old))
        except FileNotFoundError:
            verbose_info("Old file does not exist, nothing to do.")
        os.rename(full_path, full_path_old)
        os.rename(full_path_tmp, full_path)

    info("__All done!__")


def get_column_mapping(columns):
    """
    Processes the 'columns' field in the settings map to associate each field with its
    positional index in the fetched row. Also, at least one column that points to the
    file name is needed.

    Returns a map in which keys are fields ('msgid', 'msgstr', etc.) and values are indices.
    The reserved key '_file_name' points to the column index that contains file name information.
    """
    ret = {}
    found_file_name = False

    for i, column in enumerate(columns):
        if "fields" in column.keys():
            for field in column["fields"]:
                ret[field] = i
            continue

        if "static" in column.keys():
            if r"{file_name}" in column["static"]:
                ret["_file_name"] = i
                found_file_name = True

    if not found_file_name: error(('At least one of the columns must indicate the file name!\n'
                                   'e.g.: {"static": "{file_name}", "header": "FILE NAME"}'))
    return ret

def fetch_chunk(service, spreadsheet_id, range_name):
    """Fires a spreadsheet retrieval request through the Google API client."""
    result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id,
                                                 range=range_name).execute()
    values = result.get('values')
    row_count = len(values) if values is not None else 0
    info('{0} rows retrieved.'.format(row_count))
    return row_count, values

def process_chunk(file_path_map, file_handle_map, data, column_mapping, file_name_template):
    """
    Translates `data` row by row into updates to a PO file.

    Parameters:
    file_path_map - Given a file's name, points to its full path
    file_handle_map - Contains a read handle and a write handle for each file
    data - The rows obtained from the spreadsheet
    column_mapping - The column index that stores each field
    file_name_template - The value for `static` in `config.json` for the column that contains
                         the file name. Used to extract the file name.
    """
    file_name_column = column_mapping["_file_name"]
    msgid_column = column_mapping["msgid"]
    for row in data:
        if any(row):
            assigns = extract_string_assigns(file_name_template, row[file_name_column])
            verbose_info("Extracted assigns: {0}.".format(assigns))
            base_filename = assigns["file_name"]
            target_file = file_path_map[base_filename]
            info("Target file is {0}.".format(target_file))

            if base_filename not in file_handle_map.keys():
                verbose_info("Opening read and write file handles...")
                file_handle_map[base_filename] = open_handle_pair(str(target_file))

            handles = file_handle_map[base_filename]

            buffered_lines = find_msgid_in_file(handles["read"], row[msgid_column])
            write_buffered_lines_to_file(handles["write"], buffered_lines)
            write_updated_entry_to_file(handles, row, column_mapping)
    return False

def extract_string_assigns(template, string):
    """
    Given a template and a string, extract a map containing named assigns.
    e.g.: template: "{x} world!"
          string: "Hello world!"
          return: {"x": "Hello"}
    """
    escaped = re.escape(template)
    reverse_template = RE_ESCAPED_ASSIGN.sub('(?P<\\1>.*)', escaped)
    result_iterator = re.finditer(reverse_template, string)
    # Builds a dict out of named captures in a regex
    return next(result_iterator).groupdict()

def open_handle_pair(target_file):
    """
    Opens an existing .po file for reading and a new .po.tmp file for writing, copying the
    header's contents of the original file to it.
    """
    new_file = "{0}.tmp".format(target_file)
    handle_map = {"read": open(target_file, "r"), "write": open(new_file, "w")}
    # Header handling
    buffered_lines = find_msgid_in_file(handle_map["read"], "")
    write_buffered_lines_to_file(handle_map["write"], buffered_lines)
    write_updated_entry_to_file(handle_map)
    return handle_map

def find_msgid_in_file(file_handle, msgid):
    """
    Moves the file read pointer in `file_handle` to the entry's first line.
    Returns a list of all lines after the previous entry in the file.
    """
    wrapped = False
    buffered_lines = []
    pre_entry_position = 0
    reading_an_entry = False
    info("Looking for msgid __'{0}'__.".format(msgid))
    while True:
        beginning_of_line = file_handle.tell()
        line = file_handle.readline()

        # Wrap around the file once on EOF.
        if not line:
            if not wrapped:
                info("EOF reached, restarting from beginning of file...")
                wrapped = True
                buffered_lines = []
                pre_entry_position = 0
                reading_an_entry = False
                file_handle.seek(0)
            else:
                error("Msgid {0} was not found in the file.".format(msgid))

        match = RE_ENTRY_FIELD.match(line)
        if match:
            if not reading_an_entry:
                reading_an_entry = True
                pre_entry_position = beginning_of_line
            field = match.group(1)
            value = match.group(2)

            if field == 'msgid' and value == msgid:
                file_handle.seek(pre_entry_position)
                info("Successfully found! Entry begins at position __{0}__.".format(file_handle.tell()))
                return buffered_lines
        else:
            # Line does not contain a 'msg*' field. (e.g. comments or metadata)
            # These are buffered so that they can be returned later.
            if reading_an_entry:
                reading_an_entry = False
                buffered_lines = []
            # Do not buffer precedent lines containing pure strings,
            # e.g. header fields such as "Language: en\n"
            if not RE_EXTRA_STRING.match(line):
                buffered_lines.append(line)

def write_buffered_lines_to_file(file_handle, buffered_lines):
    """
    Write all lines that precede an entry to the write file's current pointer.
    These usually include comments and blank lines.
    """
    verbose_info("Buffered lines to be written: {0}".format(buffered_lines))
    for line in buffered_lines:
        file_handle.write(line)

def write_updated_entry_to_file(handles, row=None, column_mapping=None):
    """
    Reads sequential "msg*" fields from the file given by `handles`, and outputs them
    with updated values from the `row` data, mapped by `column_mapping`.

    This function expects that the pointer in both handles is at the start of an entry.
    """
    read_handle = handles["read"]
    write_handle = handles["write"]

    while True:
        beginning_of_line = read_handle.tell()
        line = read_handle.readline()

        match = RE_ENTRY_FIELD.match(line)
        can_update = (column_mapping is not None)
        if not match:
            # Preserve lines containing pure strings,
            # e.g. header fields such as "Language: en\n"
            extra_match = RE_EXTRA_STRING.match(line)
            can_update = False
            if not extra_match:
                verbose_info("Line '{0}' did not match any field. Stopping update...".format(line))
                read_handle.seek(beginning_of_line)
                break

        if can_update:
            field = match.group(1)
            verbose_info("Updating field {0}...".format(field))
            new_value = row[column_mapping[field]]
            write_handle.write('{0} "{1}"\n'.format(field, new_value))
        else:
            verbose_info("Copying line {0}...".format(line))
            write_handle.write(line)

def verbose_info(string):
    if VERBOSE: info(string)

def info(string):
    parsed = RE_HIGHLIGHT.sub('{0}\\1{1}'.format(C_BLUE, C_RESET), string)
    print(parsed)

def warn(string):
    print(C_YELLOW + string + C_RESET)

def error(string):
    raise Exception(C_RED + string + C_RESET)

if __name__ == '__main__':
    main()
