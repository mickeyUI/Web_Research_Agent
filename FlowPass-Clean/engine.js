/**
 * Google Flow Region Unlocker - Core Interception Engine
 *
 * Runs in the page's MAIN execution world at document_start.
 * Intercepts Google's internal batchexecute RPC (cPZSdc) and sets
 * the country eligibility flag at index 30 to true.
 */
(() => {
  'use strict';

  // Prevent multiple injections
  if (window.__flowUnlockerActive) return;
  window.__flowUnlockerActive = true;

  // 1. URL Handler: if landed on /unsupported-country, redirect to base URL
  if (location.pathname.endsWith('/unsupported-country')) {
    const cleanUrl = location.origin + location.pathname.replace(/\/unsupported-country$/, '') + location.search + location.hash;
    console.info('[FlowUnlocker] Detected /unsupported-country redirect. Bouncing to:', cleanUrl);
    location.replace(cleanUrl);
    return;
  }

  const TARGET_PATH = '/_/AiSandboxAngularFrontend/data/batchexecute';
  const TARGET_RPCID = 'cPZSdc';
  const TARGET_TAG = 'wrb.fr';
  const FLAG_INDEX = 30; // Google Flow eligibility boolean index
  const MIN_PAYLOAD_LEN = 32;

  const interceptedXHRs = new WeakMap();
  const patchedBodies = new WeakMap();

  /**
   * Patches the raw batch execute response text
   */
  function patchResponseText(rawText) {
    if (typeof rawText !== 'string' || !rawText.includes(TARGET_RPCID)) {
      return rawText;
    }

    const lines = rawText.split('\n');
    let modified = false;

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (!line.startsWith('[[')) continue;

      let rpcEntries;
      try {
        rpcEntries = JSON.parse(line);
      } catch {
        continue;
      }

      if (!Array.isArray(rpcEntries)) continue;

      for (const entry of rpcEntries) {
        if (!Array.isArray(entry) || entry[0] !== TARGET_TAG || entry[1] !== TARGET_RPCID) {
          continue;
        }

        // entry[2] contains the serialized JSON payload array
        let payload;
        try {
          payload = JSON.parse(entry[2]);
        } catch {
          continue;
        }

        if (Array.isArray(payload) && payload.length >= MIN_PAYLOAD_LEN) {
          const beforeValue = payload[FLAG_INDEX];
          payload[FLAG_INDEX] = true; // Overwrite region flag to true
          entry[2] = JSON.stringify(payload);
          modified = true;

          console.info(`[FlowUnlocker] Successfully patched entitlement flag at index ${FLAG_INDEX} (was: ${beforeValue} -> now: true)`);
        }
      }

      if (modified) {
        const origChunk = line.replace(/\r$/, '');
        const origLen = Number(lines[i - 1]?.replace(/\r$/, ''));
        const newChunk = JSON.stringify(rpcEntries);

        // Recalculate length prefix matching Google's chunking logic
        const utf8Len = str => new TextEncoder().encode(str).length;
        const charLen = str => str.length;
        const lenFunc = [utf8Len, charLen].find(fn => [0, 1, 2].includes(origLen - fn(origChunk))) || utf8Len;
        const lenDiff = lenFunc(newChunk) - lenFunc(origChunk);

        const hasCrLineBefore = lines[i - 1]?.endsWith('\r');
        const hasCrChunk = line.endsWith('\r');

        if (!isNaN(origLen) && i > 0) {
          lines[i - 1] = String(origLen + lenDiff) + (hasCrLineBefore ? '\r' : '');
        }
        lines[i] = newChunk + (hasCrChunk ? '\r' : '');
        break;
      }
    }

    if (modified) {
      document.documentElement.setAttribute('data-flow-unlocked', 'true');
      return lines.join('\n');
    }
    return rawText;
  }

  // 2. Hook XMLHttpRequest
  const origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    try {
      const parsed = new URL(url, location.href);
      const isTarget =
        parsed.origin === location.origin &&
        parsed.pathname.includes('/data/batchexecute') &&
        (parsed.searchParams.get('rpcids') === TARGET_RPCID ||
          parsed.searchParams.get('rpcids')?.includes(TARGET_RPCID) ||
          !parsed.searchParams.has('rpcids'));

      if (isTarget) {
        interceptedXHRs.set(this, true);
      }
    } catch {}
    return Reflect.apply(origOpen, this, [method, url, ...rest]);
  };

  for (const prop of ['responseText', 'response']) {
    const desc = Object.getOwnPropertyDescriptor(XMLHttpRequest.prototype, prop);
    if (!desc?.get || !desc.configurable) continue;

    Object.defineProperty(XMLHttpRequest.prototype, prop, {
      ...desc,
      get() {
        const originalValue = Reflect.apply(desc.get, this, []);
        if (!interceptedXHRs.get(this) || typeof originalValue !== 'string' || this.readyState !== 4) {
          return originalValue;
        }

        if (!patchedBodies.has(this)) {
          try {
            patchedBodies.set(this, patchResponseText(originalValue));
          } catch (err) {
            console.error('[FlowUnlocker] Error patching XHR response:', err);
            patchedBodies.set(this, originalValue);
          }
        }
        return patchedBodies.get(this);
      }
    });
  }

  // 3. Fallback: Hook window.fetch in case Google migrates to Fetch API
  const origFetch = window.fetch;
  if (typeof origFetch === 'function') {
    window.fetch = async function (input, init) {
      const response = await Reflect.apply(origFetch, window, [input, init]);
      try {
        const urlStr = typeof input === 'string' ? input : input instanceof Request ? input.url : '';
        const parsed = new URL(urlStr, location.href);
        if (
          parsed.origin === location.origin &&
          parsed.pathname.includes('/data/batchexecute') &&
          (parsed.searchParams.get('rpcids') === TARGET_RPCID || parsed.searchParams.get('rpcids')?.includes(TARGET_RPCID))
        ) {
          const originalText = await response.text();
          const patchedText = patchResponseText(originalText);
          return new Response(patchedText, {
            status: response.status,
            statusText: response.statusText,
            headers: response.headers
          });
        }
      } catch {}
      return response;
    };
  }

  console.info('[FlowUnlocker] Google Flow Region Unlocker active & listening.');
})();
