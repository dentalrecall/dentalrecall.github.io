# Dental news site — setup guide

This folder is a complete website that updates itself every hour:

1. It checks the dental news feeds listed in `feeds.txt`.
2. Claude writes a short summary and a "Why it matters" line for each new article.
3. The site is rebuilt and published.

Everything runs on GitHub's computers, for free. Your own computer does not need to stay on. The only cost is the Claude API, which is pay-as-you-go (expect a few dollars a month at news volume).

**What's in the folder**

| File | What it is |
|---|---|
| `build.py` | The program. The **SETTINGS** section at the top holds the site name and other options. |
| `template.html` | The look of the website. |
| `feeds.txt` | The list of news sources. Add or remove lines here. |
| `data/stories.json` | Where saved stories live. Starts empty. |
| `tests/sample_feed.xml` | Fake articles for testing. |
| `.github/workflows/update.yml` | The hourly schedule. (On a Mac this folder is hidden in Finder. Step 6 handles that.) |

---

## Step 1. Get a Claude API key

1. Go to **console.anthropic.com** and sign in (or create an account).
2. Open **Billing**, add a payment method, and buy a small amount of credit (for example $10).
3. Still in Billing, set a **monthly spend limit** (for example $20) so costs can never surprise you.
4. Open **API Keys** → **Create Key**. Name it `news-site`.
5. Copy the key (it starts with `sk-ant-`) and paste it into a note for now. You only see it once.

## Step 2. Create a GitHub account

Go to **github.com** and sign up. The free plan is all you need. Your username becomes part of the site address, for example `ahmad-dds.github.io`.

## Step 3. Create a repository (the project's home on GitHub)

1. Click the **+** at the top right → **New repository**.
2. Repository name: `dental-news` (any name works; it appears in the web address).
3. Choose **Public**. Free website hosting requires a public repository. Your API key stays private (Step 5).
4. Leave everything else as is and click **Create repository**.

## Step 4. Upload the files

1. On the new, empty repository page, click the link **uploading an existing file**.
2. Unzip the folder I sent you. Open it in Finder.
3. Select `build.py`, `template.html`, `feeds.txt`, `README.md`, and the `data` and `tests` folders, and drag them all onto the GitHub page.
4. Wait for the uploads to finish, then click the green **Commit changes** button at the bottom.

## Step 5. Add your API key as a secret

A secret is stored encrypted. Nobody, including visitors to your public repository, can read it.

1. In your repository, click **Settings** (top menu).
2. Left sidebar: **Secrets and variables** → **Actions**.
3. Click **New repository secret**.
4. Name: `ANTHROPIC_API_KEY` (exactly like that).
5. Secret: paste your key from Step 1.
6. Click **Add secret**.

## Step 6. Turn on website hosting

1. Still in **Settings**, click **Pages** in the left sidebar.
2. Under **Build and deployment** → **Source**, choose **GitHub Actions**.

## Step 7. Add the hourly schedule

This file starts with a dot, so it's hidden on a Mac. You'll create it directly on GitHub instead.

1. Go back to the main page of your repository (click `dental-news` at the top).
2. Click **Add file** → **Create new file**.
3. In the file name box, type exactly: `.github/workflows/update.yml`
   (Each `/` you type turns into a folder. That's expected.)
4. Copy everything in the box below and paste it into the large editing area:

```yaml
name: Update news site

on:
  schedule:
    - cron: "17 * * * *"     # every hour, at 17 minutes past
  workflow_dispatch:          # lets you press "Run workflow" by hand
  push:
    branches: [main]

permissions:
  contents: write
  pages: write
  id-token: write

concurrency:
  group: news-site
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Fetch feeds, summarize, build site
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: python build.py

      - name: Save new stories
        run: |
          git config user.name "news-bot"
          git config user.email "news-bot@users.noreply.github.com"
          git add data/stories.json
          git diff --cached --quiet || (git commit -m "Update stories" && git push)

      - uses: actions/configure-pages@v5

      - uses: actions/upload-pages-artifact@v3
        with:
          path: site

      - id: deployment
        uses: actions/deploy-pages@v4
```

5. Click **Commit changes** (twice, if a confirmation box appears).

Saving this file starts the first update automatically.

## Step 8. Watch the first run and open your site

1. Click the **Actions** tab at the top of the repository.
2. You'll see a run called **Update news site** with a yellow dot (running). It takes about 1–3 minutes.
3. A **green check** means it worked. Click the run; under **build** you'll see a link to your site, something like `https://YOUR-USERNAME.github.io/dental-news/`.
4. Bookmark it. From now on it refreshes every hour by itself.

To update right away at any time: **Actions** → **Update news site** (left side) → **Run workflow** → **Run workflow**.

---

## Changing things later (all in the browser)

Open any file on GitHub, click the **pencil icon** to edit, then **Commit changes**. The site rebuilds within a couple of minutes.

- **Site name:** in `build.py`, change `SITE_NAME = "The Dental Recall"`.
- **News sources:** in `feeds.txt`, add one feed address per line. To get addresses from Feedly, open a source in Feedly, click its name, and copy the feed URL shown there.
- **Cost control:** in `build.py`, lower `MAX_NEW_PER_RUN`.
- **Look:** `template.html` controls colors and fonts.

## If something goes wrong

Click the failed run (red X) in the **Actions** tab, then click the step with the red X to see the message.

| Message | Fix |
|---|---|
| `ANTHROPIC_API_KEY is not set` | Redo Step 5. The name must be exactly `ANTHROPIC_API_KEY`. |
| `API error 401` | The key was pasted wrong. Create a new key and replace the secret. |
| `API error 400` mentioning credit | Add credit in console.anthropic.com → Billing. |
| `SKIP https://...` lines | That one feed is down or changed. The rest still work. Remove or replace the line in `feeds.txt`. |
| Site shows "404" | Wait 5 minutes. Then check Step 6 (Source must be **GitHub Actions**). |
| Deploy step fails with "environment protection" | Settings → Environments → github-pages → make sure `main` is allowed. |

Send me the red message and I'll tell you what to change.

## Preview on your own computer (optional)

In Terminal, inside the unzipped folder:

```
python3 build.py --test
open site/index.html
```

This uses fake sample articles and doesn't need an API key.

## Good to know

- **Summaries come only from each publisher's public feed text**, never full articles or paywalled pages, and always link back to the original. Keep it that way.
- **AI summaries can be wrong.** The site says so in its footer. Spot-check clinical stories, especially early on.
- **Before you monetize or promote the site**, run the final name through the USPTO trademark search (tmsearch.uspto.gov) and have a trademark attorney confirm it. Also read the terms of use of your main sources.
- **A custom domain** (like `thedentalrecall.com`) can be added later in Settings → Pages.
