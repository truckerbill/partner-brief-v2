# Make.com build steps (v2, optional/legacy)

The recommended v2 path is now the **runnable Python + Makefile** flow documented in `README.md` in this folder.

Use these steps only if you specifically want to run v2 inside Make.com (UI workflow).

## Create the scenario
1. In Make.com, click **Create a new scenario**.
2. Set the scenario **Scheduling** (this is the trigger for a weekly run).
   - In the scenario editor, look for **Scheduling** (often a **clock** control in the top bar).
   - Set it to **Weekly** and choose your day/time.
   - If Make prompts for “Instantly / At regular intervals”, pick the option that lets you run **on a schedule**.

## Add partner list
3. Add **Tools → Set variable** (or **Tools → Create JSON**).
   - Name: `partners`
   - Value: paste the array from `partners.json` (the `partners` array).

## Iterate partners
4. Add **Tools → Iterator**.
   - Array: `partners`
   - This module outputs one item per loop (often **`value`** or a field named **`Partners`**, depending on your setup).

## Build the prompt (avoids JSON decode errors)
5. Add **Tools → Set variable** *after* the Iterator and *before* HTTP.
   - Name: `prompt`
   - Value: your full instruction text **on one line**, using `\n` for line breaks (do not press Enter inside the field).
   - **Do not use the `"` character** inside the prompt (double quotes break JSON when Make inserts the string into the HTTP JSON body).
   - Insert the Iterator’s partner token wherever the vendor name should go (e.g. `Partner: {{…}}` using your mapper).

Why: if `prompt` contains real newlines or unescaped `"`, Make’s inserted value turns the HTTP body into invalid JSON and Perplexity returns `bad_request` / JSON decode error.

## Call Perplexity Sonar
6. Add **HTTP → Make a request**.
   - **Method**: `POST`
   - **URL**: `https://api.perplexity.ai/v1/sonar`
   - **Authentication (top)**: **None**
   - **Headers** (add two header rows):
     - `Authorization` = `Bearer YOUR_PERPLEXITY_API_KEY`
     - `Content-Type` = `application/json`
   - **Body type**: **Custom** (if you do not see “Raw”, Custom is fine)
   - **Content type** / request type: `application/json`
   - **Body** (paste this, then map `{{prompt}}` to your **Set variable: prompt** output):

```json
{
  "model": "sonar",
  "temperature": 0.2,
  "messages": [
    { "role": "user", "content": "{{prompt}}" }
  ]
}
```

Map only the `content` value: click where `{{prompt}}` is and replace it with the variable chip from your **prompt** Set variable module (Make may show it as `3.prompt` or similar). Do **not** add extra quotes around the chip.

### What the HTTP response looks like (what you need from it)
Perplexity’s Sonar response is OpenAI-style chat-completions shaped, typically like:
- `choices[0].message.content` (the text you want)
- sometimes also a top-level `citations` array (optional)

In Make’s mapper, you want to select the field that contains the assistant’s full text response.
Most commonly this is:
- **`choices → 1 → message → content`** (Make uses 1-based numbering in the UI), or
- **`choices → 0 → message → content`** depending on how it displays arrays.

### Common HTTP misconfigurations (quick checks)
- **401 Unauthorized**: your Authorization header is wrong (must be `Bearer ...`).
- **400 Bad Request** with `JSON decode error`: the final body is not valid JSON—almost always because `prompt` had **real line breaks** or **double quotes** after mapping. Fix with the one-line `\n` prompt and no `"` in the text (see above).
- **HTML response**: you hit the wrong URL (must be `https://api.perplexity.ai/v1/sonar`).

## Aggregate into one email
7. Add **Tools → Text aggregator** (recommended) OR **Array aggregator**.
   - Source module: the HTTP module
   - **Text to aggregate**: map the assistant text field from the HTTP JSON response (see above).
   - **Separator**: `\n\n`
   - Optional: add a header/footer around the aggregated output (e.g., a date line) using plain text before/after the aggregated block.

## Send email (Gmail)
8. Add **Gmail → Send an email**.
   - To: your email
   - Subject: `Executive Partner Brief (Weekly)`
   - Body: the aggregated text

## Test
9. Click **Run once**.
10. If the scenario succeeds but the email is empty/odd:
   - Open the HTTP module output for one partner and confirm you mapped the correct field.
   - Make sure the Iterator is actually iterating over the array (you should see 8 HTTP calls if you have 8 partners).
   - If outputs are too long/short, tune the prompt (3–8 bullets max is the main lever).

## Recommended small upgrades (optional)
- Add a **Sleep** module (e.g., 0.5–1.0s) between HTTP calls if you hit rate limits.
- Add simple “guardrails” to the prompt:
  - “If you can’t find a dated source in the last 7 days, exclude it.”
  - “Prefer company newsrooms / release notes where possible.”

