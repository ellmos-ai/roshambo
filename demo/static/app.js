// Roshambo Demo Frontend — app.js
// Vanilla JavaScript, ES2020+, no external dependencies

// ============================================================================
// State & Config
// ============================================================================

// Polling, not WebSockets. Plain GETs keep the app runnable on hosts that cannot
// hold a socket open (an AWS Lambda Function URL, for one), and nothing here needs
// sub-second latency: a lease lasts minutes. All URLs are relative so the app works
// under a path prefix too -- see demo/serve.py.
const CONFIG = {
  pollIntervals: {
    health: 5000,    // Poll api/health every 5s
    status: 5000,    // Poll api/status every 5s
    claims: 3000,    // Poll api/claims every 3s (most frequent)
    denials: 3000,   // Poll api/denials every 3s -- the other half of a collision
  },
};

const state = {
  mode: 'mock',              // 'live' or 'mock'
  lastHealthDetail: '',      // Last detail string from /api/health
};

// ============================================================================
// Utility: Timestamp Formatting
// ============================================================================

/**
 * Format ISO 8601 timestamp as readable local time.
 * @param {string} isoString ISO 8601 timestamp
 * @returns {string} Formatted local time (e.g., "2:34:56 PM")
 */
function formatLocalTime(isoString) {
  try {
    const date = new Date(isoString);
    return date.toLocaleTimeString(navigator.language, {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: true,
    });
  } catch (e) {
    return isoString;
  }
}

// ============================================================================
// Utility: Error Handling & Inline Messages
// ============================================================================

/**
 * Show error message in the search error container (inline, not alert).
 * @param {string} message Error message to display
 */
function showSearchError(message) {
  const errorEl = document.getElementById('search-error');
  errorEl.textContent = message;
  errorEl.classList.add('show');
}

/**
 * Clear error message.
 */
function clearSearchError() {
  const errorEl = document.getElementById('search-error');
  errorEl.textContent = '';
  errorEl.classList.remove('show');
}

// ============================================================================
// Utility: Fetch with Error Handling
// ============================================================================

/**
 * Wrapper around fetch with basic error handling.
 * Logs errors to console but does not throw; returns null on error.
 * @param {string} url URL to fetch
 * @returns {Promise<Object|null>} Parsed JSON or null on error
 */
async function fetchJson(url) {
  try {
    const response = await fetch(url);
    if (!response.ok) {
      console.error(`Fetch error: ${response.status} ${response.statusText}`);
      return null;
    }
    return await response.json();
  } catch (error) {
    console.error(`Fetch failed for ${url}:`, error);
    return null;
  }
}

// ============================================================================
// Health Polling — Mode Banner
// ============================================================================

/**
 * Poll /api/health and update the mode banner.
 * Shows banner if mode is 'mock', hides if 'live'.
 */
async function pollHealth() {
  const data = await fetchJson('api/health');
  if (!data) return;

  // Extract mode and detail
  const mode = data.mode || 'mock';
  const detail = data.detail || '';

  state.mode = mode;
  state.lastHealthDetail = detail;

  // Update banner visibility and tooltip
  const banner = document.getElementById('mode-banner');
  if (mode === 'live') {
    banner.classList.add('mode-banner--hidden');
    banner.removeAttribute('title');
  } else {
    banner.classList.remove('mode-banner--hidden');
    if (detail) {
      banner.setAttribute('title', detail);
    } else {
      banner.removeAttribute('title');
    }
  }
}

// ============================================================================
// Status Polling — Stat Tiles
// ============================================================================

/**
 * Poll /api/status and update stat tiles and swarm_id.
 */
async function pollStatus() {
  const data = await fetchJson('api/status');
  if (!data) return;

  // Update swarm_id label
  const swarmIdEl = document.getElementById('swarm-id');
  swarmIdEl.textContent = data.swarm_id || '—';

  // Update tiles
  document.getElementById('tile-agents').textContent = data.agents || 0;
  document.getElementById('tile-active-claims').textContent = data.active_claims || 0;
  document.getElementById('tile-trails').textContent = data.trails || 0;
  document.getElementById('tile-failures').textContent = data.failures || 0;
  document.getElementById('tile-facts').textContent = data.facts || 0;
}

// ============================================================================
// Claims Polling — Table
// ============================================================================

/**
 * Render the "System" cell for one claim: a badge for the origin framework
 * (distinct colors for the AWS side vs. everything else -- the whole point of this
 * column is making two *different* agent runtimes visually obvious at a glance, not
 * just listing a framework string) plus the host label underneath.
 * @param {Object} claim A single claim from /api/claims
 * @returns {string} HTML string
 */
function renderSystemCell(claim) {
  const framework = claim.framework;
  if (!framework) {
    // Claim made by an agent_id that was never registered via
    // Roshambo.register_agent(...) (e.g. an older/manual claim) -- the LEFT JOIN in
    // demo/queries.py has nothing to report, and that is shown honestly rather than
    // guessed at.
    return '<span class="system-badge system-unknown">unknown origin</span>';
  }
  const badgeClass = `system-badge ${badgeClassFor(framework)}`;
  const host = claim.host ? `<div class="system-host">${escapeHtml(claim.host)}</div>` : '';
  return `<span class="${badgeClass}">${escapeHtml(framework)}</span>${host}`;
}

/**
 * Poll /api/claims and update the claims table.
 */
async function pollClaims() {
  const data = await fetchJson('api/claims');
  if (!data) return;

  const tbody = document.getElementById('claims-tbody');
  const claims = data.claims || [];

  if (claims.length === 0) {
    // Show empty state
    tbody.innerHTML = `
      <tr class="empty-state">
        <td colspan="6">No active claims right now.</td>
      </tr>
    `;
  } else {
    // Render each claim as a row
    tbody.innerHTML = claims.map((claim) => `
      <tr>
        <td>${escapeHtml(claim.resource || '—')}</td>
        <td>${renderSystemCell(claim)}</td>
        <td>${escapeHtml(claim.agent_id || '—')}</td>
        <td>${escapeHtml(claim.intent || '—')}</td>
        <td>${formatLocalTime(claim.acquired_at)}</td>
        <td>${formatLocalTime(claim.expires_at)}</td>
      </tr>
    `).join('');
  }
}

// ============================================================================
// Denials Polling — Table
// ============================================================================

/**
 * Poll /api/denials and update the "Turned Away" table.
 *
 * This is beat 1's second half. The winner of a race shows up in Active Claims;
 * the agents that were turned away show up here, each with the holder it was told
 * about. Rows outlive the winner's lease, because they are that agent's own
 * abandoned trail rather than a live lock (see demo/queries.py:recent_denials).
 */
async function pollDenials() {
  const data = await fetchJson('api/denials');
  if (!data) return;

  const tbody = document.getElementById('denials-tbody');
  const denials = data.denials || [];

  if (denials.length === 0) {
    tbody.innerHTML = `
      <tr class="empty-state">
        <td colspan="5">No denied claims recorded yet.</td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = denials.map((denial) => {
    // `resource`/`held_by`/`holder_intent` come from the trail's structured `detail`,
    // which only demo/run_story.py's workers write. An older abandoned trail carries
    // the same facts in prose only, so fall back to the evidence text instead of
    // printing a row of dashes and pretending nothing was recorded.
    const asked = renderSystemCell(denial);
    const holderFramework = denial.holder_framework
      ? `<span class="system-badge ${badgeClassFor(denial.holder_framework)}">${escapeHtml(denial.holder_framework)}</span>`
      : '';
    const holderIntent = denial.holder_intent
      ? `<div class="denial-intent">${escapeHtml(denial.holder_intent)}</div>`
      : `<div class="denial-intent denial-intent--prose">${escapeHtml(denial.evidence || '—')}</div>`;
    return `
      <tr>
        <td>${escapeHtml(denial.resource || '—')}</td>
        <td>${asked}</td>
        <td class="denial-holder">${escapeHtml(shortId(denial.held_by))}</td>
        <td>${holderFramework}${holderIntent}</td>
        <td>${formatLocalTime(denial.created_at)}</td>
      </tr>
    `;
  }).join('');
}

/**
 * Shorten a UUID for a table cell without losing its identity at a glance.
 * @param {string|null} id
 * @returns {string}
 */
function shortId(id) {
  if (!id) return '—';
  return id.length > 13 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id;
}

/**
 * Which badge color a framework name gets. Shared by the claims and denials tables
 * so the same runtime looks the same in both.
 * @param {string} framework
 * @returns {string}
 */
function badgeClassFor(framework) {
  const name = framework.toLowerCase();
  return name.includes('aws') || name.includes('lambda') ? 'system-aws' : 'system-local';
}

// ============================================================================
// Recall Search — Form & Results
// ============================================================================

/**
 * Escape HTML special characters to prevent injection.
 * @param {string} text Plain text to escape
 * @returns {string} Escaped HTML
 */
function escapeHtml(text) {
  const map = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#039;',
  };
  // Coerced, not assumed: several of these fields are nullable in the database
  // (`trails.agent_id`, a trail's structured `detail` keys), and a table cell is not
  // where a TypeError should surface.
  return String(text ?? '').replace(/[&<>"']/g, (m) => map[m]);
}

/**
 * Render a single recall result card.
 * @param {Object} hit A single hit from /api/recall response
 * @returns {string} HTML string
 */
function renderResultCard(hit) {
  const badgeClass = `outcome-badge ${hit.outcome}`;
  const badgeText = hit.outcome.charAt(0).toUpperCase() + hit.outcome.slice(1);

  const agentId = hit.agent_id || 'unknown';
  const distance = (hit.distance || 0).toFixed(3);
  const strength = (hit.strength || 0).toFixed(3);
  const createdAt = formatLocalTime(hit.created_at);

  let artifactHtml = '';
  if (hit.artifact_uri) {
    artifactHtml = `
      <div style="margin-top: 8px;">
        <span class="artifact-badge">
          <span>artifact:</span>
          <code>${escapeHtml(hit.artifact_uri)}</code>
        </span>
      </div>
    `;
  }

  return `
    <div class="result-card">
      <div class="result-card-header">
        <span class="${badgeClass}">${badgeText}</span>
        <div>
          <p class="result-topic">${escapeHtml(hit.topic || 'Untitled')}</p>
        </div>
      </div>
      <p class="result-approach"><strong>Approach:</strong> ${escapeHtml(hit.approach || '—')}</p>
      <p class="result-evidence"><strong>Evidence:</strong> ${escapeHtml(hit.evidence || '—')}</p>
      ${artifactHtml}
      <div class="result-metadata">
        <div class="metadata-item">
          <span class="metadata-label">Distance</span>
          <span class="metadata-value">${distance}</span>
        </div>
        <div class="metadata-item">
          <span class="metadata-label">Strength</span>
          <span class="metadata-value">${strength}</span>
        </div>
        <div class="metadata-item">
          <span class="metadata-label">Agent</span>
          <span class="metadata-value">${escapeHtml(agentId)}</span>
        </div>
        <div class="metadata-item">
          <span class="metadata-label">Created</span>
          <span class="metadata-value">${createdAt}</span>
        </div>
      </div>
    </div>
  `;
}

/**
 * Execute a recall search based on form inputs.
 * Calls /api/recall and renders results.
 */
async function executeSearch() {
  clearSearchError();

  const query = document.getElementById('search-query').value.trim();
  const limit = parseInt(document.getElementById('search-limit').value, 10) || 5;

  // Validate inputs
  if (!query) {
    showSearchError('Please enter a search query.');
    return;
  }

  if (limit < 1 || limit > 50) {
    showSearchError('Limit must be between 1 and 50.');
    return;
  }

  // Build outcomes filter: only include if at least one checkbox is checked
  const outcomeCheckboxes = document.querySelectorAll('.outcome-checkbox:checked');
  const outcomes = Array.from(outcomeCheckboxes)
    .map((cb) => cb.value)
    .join(',');

  // Build URL
  let url = `api/recall?query=${encodeURIComponent(query)}&limit=${limit}`;
  if (outcomes) {
    url += `&outcomes=${outcomes}`;
  }

  // Fetch results. Not using fetchJson() here on purpose: FastAPI's
  // HTTPException puts the useful message in a `detail` field on the JSON
  // body even for 4xx/5xx responses, and fetchJson() collapses any non-2xx
  // response to `null`, which would throw that message away.
  let response;
  let data;
  try {
    response = await fetch(url);
    data = await response.json().catch(() => null);
  } catch (error) {
    console.error(`Fetch failed for ${url}:`, error);
    showSearchError('Failed to fetch search results. The API may be unreachable.');
    return;
  }

  if (!response.ok) {
    const message = (data && data.detail) ? data.detail : `HTTP ${response.status}`;
    showSearchError(`API error: ${message}`);
    return;
  }

  if (!data) {
    showSearchError('Failed to fetch search results. The API may be unreachable.');
    return;
  }

  // Render results
  const resultsEl = document.getElementById('recall-results');
  const hits = data.hits || [];

  if (hits.length === 0) {
    resultsEl.innerHTML = `
      <div class="empty-results">
        No prior attempts found for this query.
      </div>
    `;
  } else {
    resultsEl.innerHTML = hits.map((hit) => renderResultCard(hit)).join('');
  }
}

/**
 * Attach search form event listeners.
 */
function attachSearchListeners() {
  const searchButton = document.getElementById('search-button');
  const searchInput = document.getElementById('search-query');

  // Search button click
  searchButton.addEventListener('click', executeSearch);

  // Enter key in search input
  searchInput.addEventListener('keypress', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      executeSearch();
    }
  });
}

// ============================================================================
// Polling Setup
// ============================================================================

/**
 * Start all polling intervals.
 */
function startPolling() {
  // Initial immediate poll -- without this the first screenful is empty for a full
  // interval, which matters when someone is screenshotting or recording the page.
  pollHealth();
  pollStatus();
  pollClaims();
  pollDenials();

  // Set up intervals
  setInterval(pollHealth, CONFIG.pollIntervals.health);
  setInterval(pollStatus, CONFIG.pollIntervals.status);
  setInterval(pollClaims, CONFIG.pollIntervals.claims);
  setInterval(pollDenials, CONFIG.pollIntervals.denials);
}

// ============================================================================
// Initialization
// ============================================================================

/**
 * Initialize the app on page load.
 */
document.addEventListener('DOMContentLoaded', () => {
  attachSearchListeners();
  startPolling();
});
