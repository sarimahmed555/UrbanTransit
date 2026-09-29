import { NAV_GROUPS, PAGE_BY_ID } from '../navigation.js';
import { icon } from './icons.js';
import { button, escapeHtml, iconButton, sourceTag, statusDot } from './ui.js';

export function renderSidebar({ activePage = 'executive', open = false, mode = 'demo', demoMode = false, apiConnection = 'checking' } = {}) {
  const apiStatus = apiConnection === 'connected'
    ? { label: 'EVIDENCE API CONNECTED', detail: 'Audited model results available', tone: 'good' }
    : apiConnection === 'unavailable'
      ? { label: 'EVIDENCE API UNAVAILABLE', detail: 'Verified results could not be loaded', tone: 'error' }
      : { label: 'CONNECTING TO EVIDENCE API', detail: 'Checking local backend', tone: 'pending' };
  const groups = NAV_GROUPS.map((group) => `<div class="sidebar__group">
    <p class="sidebar__group-label">${escapeHtml(group.label)}</p>
    <nav class="sidebar__nav" aria-label="${escapeHtml(group.label)}">
      ${group.items.map((item) => `<a class="nav-item ${item.id === activePage ? 'is-active' : ''}" href="#/${item.id}" data-page="${escapeHtml(item.id)}" ${item.id === activePage ? 'aria-current="page"' : ''}><span class="nav-item__icon">${icon(item.icon, 18)}</span><span class="nav-item__label">${escapeHtml(item.label)}</span>${item.id === activePage ? '<span class="nav-item__active-bar"></span>' : ''}</a>`).join('')}
    </nav>
  </div>`).join('');

  return `<aside class="sidebar ${open ? 'is-open' : ''}" data-sidebar>
    <div class="sidebar__brand">
      <a class="brand-mark" href="#/executive" aria-label="UrbanTransit IQ home"><span class="brand-mark__symbol">UT</span><span class="brand-mark__copy"><strong>UrbanTransit <em>IQ</em></strong><small>TransitVerse Intelligence</small></span></a>
      <button class="sidebar__close" type="button" data-action="close-sidebar" aria-label="Close navigation">${icon('close', 19)}</button>
    </div>
    <div class="sidebar__mode">${sourceTag(mode, mode === 'demo' ? 'DEMO PREVIEW' : apiStatus.label)}<span>${mode === 'demo' ? 'Synthetic UI fixtures' : apiStatus.detail}</span></div>
    <div class="sidebar__scroll"><div class="sidebar__nav-wrap">${groups}</div></div>
    <div class="sidebar__footer">
      <div class="sidebar__workspace"><span class="workspace-avatar">UT</span><span><strong>UrbanTransit IQ</strong><small>${demoMode ? 'Demo Mode · no login' : 'Authenticated session'}</small></span><button type="button" class="workspace-menu" data-action="navigate-profile" aria-label="Account and session">${icon('more', 16)}</button></div>
      <div class="sidebar__connection"><span>${statusDot(mode === 'demo' ? 'demo' : apiStatus.tone)}</span><span>${mode === 'demo' ? 'Demo mode active' : apiStatus.label}</span></div>
    </div>
  </aside>`;
}

export function renderHeader({ activePage = 'executive', mode = 'demo', apiConnection = 'checking', searchValue = '', refreshing = false, filterOpen = false } = {}) {
  const page = PAGE_BY_ID[activePage] ?? PAGE_BY_ID.executive;
  const connected = apiConnection === 'connected';
  return `<header class="topbar"><div class="topbar__left"><button class="topbar-icon" type="button" data-action="toggle-sidebar" aria-label="Toggle navigation">${icon('menu', 20)}</button><div class="breadcrumbs"><span>Control room</span><span class="breadcrumbs__slash">/</span><strong>${escapeHtml(page.label)}</strong></div></div><div class="topbar__right"><label class="global-search"><span class="sr-only">Search pages</span>${icon('search',16)}<input type="search" value="${escapeHtml(searchValue)}" placeholder="Find a workspace…" data-action="global-search" /></label><a class="connection-label" href="#/system">${statusDot(connected ? 'good' : 'pending')}${connected ? 'Evidence connected' : apiConnection === 'checking' ? 'Connecting…' : 'Evidence unavailable'}</a><button class="refresh-button" type="button" data-action="refresh" ${refreshing ? 'disabled' : ''}>${icon('refresh',16)}<span>${refreshing ? 'Refreshing' : 'Refresh evidence'}</span></button></div></header>`;
}

export function renderShell({ activePage = 'executive', mode = 'demo', demoMode = false, apiConnection = 'checking', content = '', searchValue = '', refreshing = false, filterOpen = false, sidebarOpen = false, sidebarCollapsed = false } = {}) {
  const footerStatus = mode === 'demo'
    ? 'Synthetic demo preview active'
    : apiConnection === 'connected'
      ? 'Evidence API connected · operational analytics unavailable'
      : apiConnection === 'unavailable'
        ? 'Evidence API unavailable · verified results hidden'
        : 'Connecting to local evidence API';
  return `<div class="app-shell ${sidebarCollapsed ? 'is-collapsed' : ''}">${renderSidebar({ activePage, mode, demoMode, apiConnection, open: sidebarOpen })}<div class="app-main">${renderHeader({ activePage, mode, apiConnection, searchValue, refreshing, filterOpen })}<main id="main-content" class="main-content" tabindex="-1">${content}</main><footer class="app-footer"><span>UrbanTransit IQ · TransitVerse Intelligence</span><span>${escapeHtml(demoMode ? `Demo Mode · ${footerStatus}` : footerStatus)}</span><button type="button" data-action="open-about">Evidence policy ${icon('chevronRight', 13)}</button></footer></div></div>`;
}

export function renderMobileOverlay(open) {
  return open ? '<button class="mobile-overlay" type="button" data-action="close-sidebar" aria-label="Close navigation"></button>' : '';
}
