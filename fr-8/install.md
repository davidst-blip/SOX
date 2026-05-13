# FR-8 Skill — Installation Guide

**One-time setup. Estimated time: 30 minutes.**

---

## Prerequisites

| Requirement | How to get it |
|---|---|
| Claude Code CLI | `npm install -g @anthropic-ai/claude-code` |
| Anthropic API key | console.anthropic.com → API Keys |
| Python 3.10+ | Already on your machine or `sudo apt install python3` |
| Python packages | `pip install openpyxl requests` |
| NetSuite TBA credentials | Ask your NS admin for an Integration Record → Token-Based Auth |
| Git (optional but recommended) | `sudo apt install git` |

---

## Step 1 — Set your Anthropic API key

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# Add to ~/.bashrc or ~/.zshrc to persist:
echo 'export ANTHROPIC_API_KEY=sk-ant-...' >> ~/.bashrc
```

---

## Step 2 — Install Python dependencies

```bash
pip install openpyxl requests
# Verify:
python3 -c "import openpyxl, requests; print('OK')"
```

---

## Step 3 — Copy the skill files

If you're running from this repo (recommended):

```bash
mkdir -p ~/.claude/skills
cp -r /path/to/this/fr-8 ~/.claude/skills/fr-8
```

Or clone directly:

```bash
git clone https://github.com/davidst-blip/sox ~/.claude/skills/sox-skills
ln -s ~/.claude/skills/sox-skills/fr-8 ~/.claude/skills/fr-8
```

---

## Step 4 — Configure NetSuite MCP

Create or update `~/.claude/mcp.json` (add the NetSuite block if it's not already there):

```json
{
  "mcpServers": {
    "netsuite": {
      "command": "npx",
      "args": ["-y", "@netsuite-mcp/server"],
      "env": {
        "NS_ACCOUNT_ID":       "YOUR_ACCOUNT_ID",
        "NS_CONSUMER_KEY":     "YOUR_CONSUMER_KEY",
        "NS_CONSUMER_SECRET":  "YOUR_CONSUMER_SECRET",
        "NS_TOKEN_ID":         "YOUR_TOKEN_ID",
        "NS_TOKEN_SECRET":     "YOUR_TOKEN_SECRET"
      }
    }
  }
}
```

Replace the placeholder values with your TBA credentials from the NS admin.

See `mcp.json.template` in this folder for a copyable template.

> **Security note:** Do not commit `~/.claude/mcp.json` to any repository.
> The file contains credentials. If you version-control your `.claude` config,
> add `mcp.json` to `.gitignore`.

---

## Step 5 — Set the output directory

```bash
# Set to your Google Drive SOX folder (adjust path as needed):
export FR8_OUTPUT_DIR="/mnt/g/Shared drives/SOX/SOX404/Periods/2026/Matrix/PH2 Testing/FR-8"
# Add to ~/.bashrc to persist:
echo 'export FR8_OUTPUT_DIR="/mnt/g/Shared drives/SOX/SOX404/Periods/2026/Matrix/PH2 Testing/FR-8"' >> ~/.bashrc
```

If this variable is not set, workbooks are saved to `/tmp/`.

---

## Step 6 — Version-control the skill (recommended for audit trail)

```bash
cd ~/.claude/skills/fr-8
git init
git add .
git commit -m "FR-8 skill v1.0.0 — initial install"
git tag v1.0.0
```

The script reads the git tag automatically via:
```bash
git -C ~/.claude/skills/fr-8 describe --tags
```

This tag appears in the IPE Evidence tab of every workbook, proving to auditors
that no mid-year logic change occurred between runs.

---

## Step 7 — Run a demo test

```bash
python3 ~/.claude/skills/fr-8/scripts/run_fr8.py \
    --demo \
    --period-start 2026-04-01 --period-end 2026-04-30 \
    --output /tmp/FR8_demo_Apr2026.xlsx
```

Expected output:
```
Running in DEMO mode — synthetic data, zero-gap scenario
Running reconciliation...
  243 comparisons: 243 OK, 0 exceptions, 0 missing, 0 carry-fwd
Building workbook...

OK: Workbook saved → /tmp/FR8_demo_Apr2026.xlsx
```

Open the xlsx and verify the Dashboard shows "PASS — No exceptions identified".

---

## Step 8 — Run for a real period

Start Claude Code:
```bash
claude
```

Type:
```
run FR-8 for April 2026
```

Claude Code will:
1. Confirm the period
2. Pull NetSuite rates via MCP
3. Fetch BOI rates from the live API
4. Generate the working paper
5. Tell you the output path

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: openpyxl` | `pip install openpyxl` |
| `ModuleNotFoundError: requests` | `pip install requests` |
| `NS MCP error: Unknown field basecurrency` | NS tenant uses different column names. Tell Claude to try the saved-search fallback path. |
| `BOI API: 403 Cloudflare` | Download CSV from boi.org.il and run with `--boi-csv path/to/file.csv` |
| `Parsed 0 observations from BOI` | Run `fetch_boi.py` with `--debug` to see raw CSV columns; open an issue in the repo |
| `FR8_OUTPUT_DIR` not set, file lands in `/tmp/` | `export FR8_OUTPUT_DIR=...` and re-run |
| `git describe` returns no tag | Run `git tag v1.0.0` in the skill folder |
| `No comparisons (all BOI_NO_PUBLISH)` | Check `israeli-holidays.json` — possibly Chol HaMoed dates are incorrectly listed as holidays |

---

## Monthly Checklist

At month-end (first week of the following month):

- [ ] Open Claude Code: `claude`
- [ ] Type: `run FR-8 for [Month] [Year]`
- [ ] Confirm period when prompted
- [ ] Wait for workbook output (~30 seconds)
- [ ] Open workbook → check Dashboard for PASS/REVIEW
- [ ] If exceptions: review Exceptions tab, add investigation notes
- [ ] Add Reviewer Notes to Dashboard
- [ ] Sign the "Reviewed by" row
- [ ] Save to Google Drive SOX evidence folder
- [ ] File as FR-8 evidence in the period binder
