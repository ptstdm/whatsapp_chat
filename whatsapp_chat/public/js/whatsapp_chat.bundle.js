import {
  ChatList,
  get_settings,
} from './components';

frappe.provide('frappe.Chat');
frappe.provide('frappe.Chat.settings');

// Register as a standard page — frappe.views.pageview.with_page checks this
// first (before any DB lookup), creates the wrapper synchronously, then data
// loads async. This is the same pattern used by frappe's Workspaces page.
frappe.standard_pages['whatsapp'] = function () {
  const wrapper = frappe.container.add_page('whatsapp');

  frappe.ui.make_app_page({
    parent: wrapper,
    title: __('WhatsApp'),
    single_column: true,
  });

  frappe.Chat._setup_page_layout(wrapper);

  $(wrapper).bind('show', function () {
    frappe.Chat._fit_height();
    frappe.Chat._handle_route_params();
  });

  $(window).off('resize.wa_chat').on('resize.wa_chat', () => frappe.Chat._fit_height());
};

frappe.Chat = class {
  constructor() {
    this._setup();
  }

  async _setup() {
    try {
      if (!('desk' in frappe)) return;

      const token = localStorage.getItem('guest_token') || '';
      const res = await get_settings(token);

      if (res.enable_chat === false || !res.is_admin) return;

      frappe.Chat.settings = frappe.Chat.settings || {};
      frappe.Chat.settings.user = res.user_settings;
      frappe.Chat.settings.unread_count = frappe.Chat.settings.unread_count || 0;
      frappe.Chat._config = res;

      await frappe.socketio.init(res.socketio_port);
      this._inject_navbar_icon();
    } catch (err) {
      console.error('WhatsApp Chat init error:', err);
    }
  }

  _inject_navbar_icon() {
    if ($('.chat-navbar-icon').length) return;

    const html = `
      <li class='nav-item dropdown dropdown-notifications dropdown-mobile chat-navbar-icon'
          title="${__('WhatsApp')}">
        ${frappe.utils.icon('small-message', 'md')}
        <span class="badge" id="chat-notification-count"></span>
      </li>
    `;
    $('header.navbar > .container > .navbar-collapse > ul').prepend(html);

    $('.chat-navbar-icon').on('click', () => window.open('/app/whatsapp', '_blank'));
  }

  // ── Page lifecycle ────────────────────────────────────────────────────────

  static _setup_page_layout(wrapper) {
    const page = wrapper.page;
    const $main = $(page.main).addClass('wa-main').empty();
    const $filter_bar = $('<div class="wa-filters-bar"></div>');
    const $wa_page = $('<div class="wa-page"></div>');
    const $left   = $('<div class="wa-panel-left"></div>');
    const $right  = $('<div class="wa-panel-right"></div>');

    $right.html(frappe.Chat._empty_state_html());
    $wa_page.append($left, $right);
    $main.append($filter_bar, $wa_page);

    frappe.Chat._fit_height();
    frappe.Chat._init_chat_list($left, $right, $filter_bar);
  }

  // Size the chat area to exactly fill from its top to the viewport bottom, so
  // the message input is always visible (no page scrollbar). Measured at runtime
  // because the page-head/navbar heights vary by theme.
  static _fit_height() {
    const $main = $('.wa-main');
    if (!$main.length) return;
    const top = $main[0].getBoundingClientRect().top;
    if (top > 0) {
      $main.css('height', `calc(100vh - ${Math.round(top)}px)`);
    }
  }

  static async _init_chat_list($left, $right, $filter_bar) {
    if (frappe.Chat._page_ready) return;

    let res = frappe.Chat._config;
    if (!res) {
      res = await get_settings('');
      frappe.Chat._config = res;
      frappe.Chat.settings = frappe.Chat.settings || {};
      frappe.Chat.settings.user = res.user_settings;
      frappe.Chat.settings.unread_count = 0;
      await frappe.socketio.init(res.socketio_port);
    }

    if (!res || !res.is_admin) return;

    frappe.Chat._chat_list = new ChatList({
      $wrapper:         $left,
      $space_wrapper:   $right,
      $filters_wrapper: $filter_bar,
      user:             res.user,
      user_email:       res.user_email,
      is_admin:         res.is_admin,
    });
    frappe.Chat._chat_list.render();
    frappe.Chat._page_ready = true;

    frappe.Chat._handle_route_params();
  }

  static _empty_state_html() {
    return `
      <div class="wa-empty-state">
        <div class="wa-empty-icon">${frappe.utils.icon('small-message', 'xl')}</div>
        <h4>${__('Select a conversation')}</h4>
        <p class="text-muted">${__('Choose a chat from the left panel to start messaging.')}</p>
      </div>
    `;
  }

  // Auto-open a room when the URL carries a phone number:
  // frappe.set_route('whatsapp', '918928471344')
  static _handle_route_params() {
    const route = frappe.get_route();
    if (route && route.length > 1) {
      frappe.Chat._open_room_by_phone(String(route[1]));
    }
  }

  static _open_room_by_phone(phone) {
    if (!frappe.Chat._chat_list) return;
    const last10 = phone.slice(-10);

    const match = frappe.Chat._chat_list.chat_rooms.find(([, room]) =>
      String(room.profile.user_email || '').slice(-10) === last10
    );

    if (match) match[1].$chat_room.trigger('click');
  }
};

$(function () {
  new frappe.Chat();
});
