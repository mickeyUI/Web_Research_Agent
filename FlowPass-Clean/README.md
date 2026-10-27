# Google Flow Region Unlocker (FlowPass Clean)

A clean, lightweight, 100% free Chrome extension (Manifest V3) that unlocks **Google Flow** (`flow.google.com`) in all countries.

## Why this exists
Google Flow restricts access based on regional availability and redirects unsupported users to `/unsupported-country`. Commercial extensions like FlowPass charge subscriptions or enforce 3-hour trial timers using remote license validation.

This extension does the exact same bypass directly in your browser with:
- **Zero remote telemetry or license checks**
- **No background service workers draining memory**
- **No paid subscription or countdown timers**
- **Pure Manifest V3 implementation running at `document_start`**

## How it works
1. Injects `engine.js` into the page's execution world (`MAIN`).
2. Intercepts Google's internal `batchexecute` RPC (`cPZSdc`).
3. Overwrites region eligibility flag (`index 30`) from `false` to `true`.
4. Recalculates chunk byte-length headers so Google's Angular client accepts the response.
5. Automatically redirects away from `/unsupported-country` if hit.

## Installation in Chrome / Brave / Edge
1. Open your browser and navigate to:
   ```
   chrome://extensions/
   ```
2. Enable **Developer mode** (toggle in the top-right corner).
3. Click **Load unpacked**.
4. Select this directory:
   ```
   C:\Users\YOGA\Documents\My_Repo\Python\webResearchAgent\FlowPass-Clean
   ```
5. Navigate to [https://flow.google.com/](https://flow.google.com/).
