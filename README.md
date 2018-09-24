# gettext2sheets
A script to help manage .po files used in [Gettext](https://www.gnu.org/software/gettext/) by syncing their contents to Google Sheets.

![Demo Spreadsheet](https://raw.githubusercontent.com/gVirtu/gettext2sheets/gh-pages/gettext2sheets.png)

## How to use

0. Make sure you have Python 3 installed and, preferably, [virtualenv](https://virtualenv.pypa.io/).
1. Run `pip install -r requirements.txt` to fetch dependencies
2. You'll need a credentials file that will authorize calls to Google Sheets API.
    1. Create a new project [here](https://console.developers.google.com/flows/enableapi?apiid=sheets.googleapis.com).
    2. Click **"Go to credentials"**
    3. Choose **Google Sheets API** in "Which API are you using?" and **Other UI** in "Where will you be calling the API from?"
    4. Choose **User data** in "What data will you be accessing?"
    5. Give a name to your OAuth 2.0 client ID and click **"Create OAuth client ID"**
    6. In the "Download credentials" step, hit **"Download"** and name the file `credentials.json`
    7. Make sure the file is in the same directory as `gettext2sheets.py`.
3. Configure the script by opening `config.json`:
    1. Replace `path` with the directory that contains all locale folders. (each locale folder should contain a folder named LC_MESSAGES with one or more .po files)
    2. Replace `spreadsheet_id` with the target spreadsheet ID. To obtain this, open a spreadsheet in your browser and copy the ID from the URL, which should be of the form: `https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID_GOES_HERE>/edit`
    3. Configure `locales` by making sure `sheet` references a valid sheet in your document. By default, new sheets come with a single sheet named 'Sheet1'. 
    4. (Optional) Check the **Configuration** section below for more detail.

To push the contents of .po files to Google Sheets: 
```
python gettext2sheets.py push
```

**Note**: When doing this for the first time, you'll have to complete Google's authentication flow. A file named `token.json` will be saved locally and will be used in subsequent runs.

To pull the contents of .po files from Google Sheets: 

```python gettext2sheets.py pull```

**Note**: Only `msgids` that are present in the local .po files will be persisted, everything else is discarded. In case of anything unexpected, the unmodified `.po` files are stored with the extension `.po.old`. **These files will be overwritten if you run the pull command again. Be careful!**

For detailed output, you may enable the flag `-v` or `--verbose`.

## Configuration

Gettext2sheets supports the following settings in `config.json`:

| Key      | Meaning |
|----------|-------------------------------------------------------------------------------------------------------|
| `path`     | Absolute or relative path to gettext's base directory. It should contain the .po files to be synced. |
| `spreadsheet_id` | The 44-character ID for the target spreadsheet. Obtained from its URL: `https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID_GOES_HERE>/edit`|
| `pull_chunk_size` | Each pull operation is done in chunks, each chunk corresponds to a request to the Sheets API. This option determines the size of a chunk. |
| `locales` | Describes how data for a specific locale will be laid out in the spreadsheet. | 
| `locales/XX/sheet` | Name of the sheet in which the data will be inserted. | 
| `locales/XX/row_offset` | Vertical shift, in rows (if zero, the data will start at row 1) |
| `locales/XX/column_offset` | Horizontal shift, in columns (if zero, the data will start at column A) |
| `locales/XX/columns` | List of column descriptors that describe the arrangement of data fields. More below. |

### Column descriptors

In the resulting datasheet, each row always maps to an entry from a .po file. Columns, however, can be arranged according to preference. Gettext entries can contain multiple fields such as `msgid`, `msgstr`, `msgid_plural`, `msgstr[0]`, `msgctxt`, etc. Through the configuration file, you can describe which field goes into each column, and what the header for this column is called. For example:

```json
  {"fields": ["msgstr", "msgstr[0]"], "header": "TRANSLATED (SINGULAR)"}
```

The `fields` key lists, in order of priority, the fields that may populate this column. In this example, if `msgstr` is present, it will be used. Otherwise, `msgstr[0]` will be used. The `header` contains a literal string that will be used as a header.

Columns can also contain static text via the `static` key. The value will be parsed, replacing **assigns** in curly brace notation with metadata. An example is the column that displays the file name:

```json
  {"static": "File: {file_name}", "header": "FILE NAME"}
```

If the row refers to file `messages.po`, then the resulting value in this column will be `File: messages.po`. The following metadata can be inserted:

| Key      | Meaning |
|----------|-------------------------------------------------------------------------------------------------------|
| `{file_name}`     | Basename for the file that contains the entry. **This must be present in at least one column**. |
| `{locale}` | Identifier for the locale. e.g.: `en`, `pt-BR`, etc. |
| `{timestamp}` | A datetime string corresponding to the moment when the request body was built. |

## Remarks

I am not responsible for any damage caused by the use of this script. Remember to use Sheets' **Version History** feature should things go wrong somehow. Please report issues you might encounter, other contributions are also welcome!
