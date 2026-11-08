# Demo workflow

Reproduce a full demo in ~5 minutes. Needs a Gemini API key and 4+ product photos.

## 1. Start the API (terminal 1)
```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```
Check: `curl.exe http://127.0.0.1:8000/health` -> `{"status":"ok"}`

## 2. Start the UI (terminal 2)
```powershell
.\.venv\Scripts\Activate.ps1
streamlit run streamlit_app/app.py
```

## 3. Run the four demos
For each product below: upload the photo(s) -> **Generate Listing** -> check the result.

| # | Product | Expected |
|---|---|---|
| 1 | Phone (1-2 photos) | Category *Mobiles and Tablets*, tags 5-8, no storage/warranty claims |
| 2 | Tablet | Brand only if visible; accessories "pictured with", never "includes" |
| 3 | Shoes | Category *Fashion*, condition `Unable to Determine` unless wear is visible |
| 4 | Watch | Category *Electronics*, no battery/water-resistance claims |

Pass criteria for every demo: valid JSON, title <= 70 chars, 5-8 lowercase tags, nothing in the text that
is not visible in the photo.

## 4. Show the failure handling
* Upload a landscape photo (`evaluation/images/landscape.jpg`) -> "no product detected" message.
* Rename a `.txt` file to `.jpg` and upload -> "not a readable image".
* Stop the API and click Generate -> "cannot reach the API" message, no crash.

## 5. Same thing from the command line
```powershell
curl.exe -s -F "images=@sample_images\phone.jpg" http://127.0.0.1:8000/api/v1/generate-listing/
```
(Note the trailing slash.) Use several `-F "images=@..."` for multiple views of one product.
