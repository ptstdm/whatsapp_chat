import ChatRoom from './chat_room';
import ChatAddRoom from './chat_add_room';
import ChatUserSettings from './chat_user_settings';
import { get_rooms, mark_message_read, get_time } from './chat_utils';

export default class ChatList {
  constructor(opts) {
    this.$wrapper = opts.$wrapper;
    // In two-panel (full-screen) mode the caller passes a separate wrapper
    // for ChatSpace so the left panel is never replaced by a chat view.
    this.$space_wrapper = opts.$space_wrapper || opts.$wrapper;
    this.user = opts.user;
    this.user_email = opts.user_email;
    this.is_admin = opts.is_admin;
    this.setup();
  }

  setup() {
    this.$chat_list = $(document.createElement('div'));
    this.$chat_list.addClass('chat-list');
    this.chat_rooms = [];
    this.setup_header();
    this.setup_search();
    this.fetch_and_setup_rooms();
    this.setup_socketio();
  }

  setup_header() {
    const chat_list_header_html = `
    <div class='chat-list-header'>
      <div class='header-left'>
        <h3>${__('Chats')}</h3>
        <label class='unread-filter-container'>
          <input type='checkbox' class='unread-filter-checkbox'>
          <span class='checkmark'>${__('Unread')}</span>
        </label>
      </div>
      <div class='chat-list-icons'>
        <div class='add-room' title='Create Private Room'>
          ${frappe.utils.icon('users', 'md')}
        </div>
        <div class='user-settings' title='Settings' style="display:none">
          ${frappe.utils.icon('setting-gear', 'md')}
        </div>
      </div>
    </div>
  `;
    this.$chat_list.append(chat_list_header_html);
  }

  setup_search() {
    const chat_list_search_html = `
		<div class='chat-search'>
			<div class='input-group'>
				<input class='form-control chat-search-box'
				type='search' 
				placeholder='${__('Search conversation')}'
				>	
				<span class='search-icon'>
					${frappe.utils.icon('search', 'sm')}
				</span>
			</div>
		</div>
		`;
    this.$chat_list.append(chat_list_search_html);
  }

  async fetch_and_setup_rooms() {
    try {
      const res = await get_rooms(this.user_email);
      this.rooms = res;
      this.setup_rooms();
      this.render_messages();
    } catch (error) {
      frappe.msgprint({
        title: __('Error'),
        message: __('Something went wrong. Please refresh and try again.'),
      });
      console.error(error);
    }
  }

  setup_rooms() {
    this.$chat_rooms_container = $(document.createElement('div'));
    this.$chat_rooms_container.addClass('chat-rooms-container');
    this.chat_rooms = [];
    //console.log(this.rooms)
    this.rooms.forEach((element) => {
      let profile = {
        user: this.user,
        user_email: element.mobile_no,
        last_message: element.last_message,
        last_date: element.modified,
        is_admin: this.is_admin,
        room: element.name,
        is_read: element.is_read,
        room_name: element.contact_name,
        room_type: element.type,
        opposite_person_email: element.mobile_no,
        message_type: element.message_type
      };

      this.chat_rooms.push([
        profile.room,
        new ChatRoom({
          $wrapper: this.$space_wrapper,
          $chat_rooms_container: this.$chat_rooms_container,
          chat_list: this,
          element: profile,
        }),
      ]);
    });
    this.$chat_list.append(this.$chat_rooms_container);
  }

  filter_rooms(query, showOnlyUnread = false) {
    for (const room of this.chat_rooms) {
      const txt = room[1].profile.room_name.toLowerCase();
      const matchesSearch = query === '' || txt.includes(query);

      let matchesFilter = true;
      if (showOnlyUnread) {
        // Show only unread incoming messages
        matchesFilter = room[1].profile.is_read === 0 &&
          room[1].profile.message_type === 'Incoming';
      }

      if (matchesSearch && matchesFilter) {
        room[1].$chat_room.show();
      } else {
        room[1].$chat_room.hide();
      }
    }
  }

  create_new_room(profile) {
    this.chat_rooms.unshift([
      profile.room,
      new ChatRoom({
        $wrapper: this.$space_wrapper,
        $chat_rooms_container: this.$chat_rooms_container,
        chat_list: this,
        element: profile,
      }),
    ]);
    this.chat_rooms[0][1].render('prepend');
  }

  setup_events() {
    const me = this;

    $('.chat-search-box').on('input', function (e) {
      const query = $(this).val().toLowerCase();
      const showOnlyUnread = $('.unread-filter-checkbox').is(':checked');
      me.filter_rooms(query, showOnlyUnread);
    });

    // Handle unread filter checkbox
    $('.unread-filter-checkbox').on('change', function (e) {
      const showOnlyUnread = $(this).is(':checked');
      const query = $('.chat-search-box').val().toLowerCase();
      me.filter_rooms(query, showOnlyUnread);
    });

    $('.add-room').on('click', function (e) {
      if (typeof me.chat_add_room_modal === 'undefined') {
        me.chat_add_room_modal = new ChatAddRoom({
          user: me.user,
          user_email: me.user_email,
        });
      }
      me.chat_add_room_modal.show();
    });

    $('.user-settings').on('click', function (e) {
      if (typeof me.chat_user_settings === 'undefined') {
        me.chat_user_settings = new ChatUserSettings();
      }
      me.chat_user_settings.show();
    });
  }

  render_messages() {
    this.$chat_rooms_container.empty();
    for (const element of this.chat_rooms) {
      element[1].render('append');
    }
  }

  render() {
    this.$wrapper.html(this.$chat_list);
    this.setup_events();
  }

  move_room_to_top(chat_room_item) {
    this.chat_rooms = [
      chat_room_item,
      ...this.chat_rooms.filter((item) => item !== chat_room_item),
    ];
  }

  setup_socketio() {
    const me = this;
    frappe.realtime.on('latest_chat_updates', function (res) {
      //Find the room with the specified room id
      const chat_room_item = me.chat_rooms.find(
        (element) => element[0] === res.room
      );

      if (typeof chat_room_item === 'undefined') {
        return;
      }

      frappe.utils.play_sound('chat-message-receive');
      const message =
        res.content.length > 24
          ? res.content.substring(0, 24) + '...'
          : res.content;

      chat_room_item[1].set_last_message(message, res.creation);

      // In two-panel (full-screen) mode both .chat-list and .chat-space are
      // visible simultaneously. Detect this by checking for .wa-panel-right.
      const is_two_panel = $('.wa-panel-right').length > 0;
      const right_panel_space = $('.wa-panel-right .chat-space');

      if (is_two_panel) {
        // Two-panel: check which room is open in the right panel
        const current_room = right_panel_space.attr('data-current-room');

        if (current_room === res.room) {
          const active_chat_space = chat_room_item[1].chat_space;
          if (active_chat_space && typeof active_chat_space.receive_message === 'function') {
            active_chat_space.receive_message(res, get_time(res.creation));
            mark_message_read(res.room);
          }
        } else if (res.sender_user_no !== me.user_email) {
          chat_room_item[1].set_as_unread();
        }

        chat_room_item[1].move_to_top();
        me.move_room_to_top(chat_room_item);
        me.refresh_current_filter();
      } else if ($('.chat-list').is(':visible')) {
        // Floating widget: user is on the chat list screen
        if (res.sender_user_no !== me.user_email) {
          chat_room_item[1].set_as_unread();
        }
        chat_room_item[1].move_to_top();
        me.move_room_to_top(chat_room_item);
        me.refresh_current_filter();
      } else if ($('.chat-space').is(':visible')) {
        // Floating widget: user has a chat space open
        const current_room = $('.chat-space').attr('data-current-room');

        if (current_room === res.room) {
          const active_chat_space = chat_room_item[1].chat_space;
          if (active_chat_space && typeof active_chat_space.receive_message === 'function') {
            active_chat_space.receive_message(res, get_time(res.creation));
            mark_message_read(res.room);
          }
        } else if (res.sender_user_no !== me.user_email) {
          chat_room_item[1].set_as_unread();
        }

        chat_room_item[1].move_to_top();
        me.move_room_to_top(chat_room_item);
      } else {
        if (res.sender_user_no !== me.user_email) {
          chat_room_item[1].set_as_unread();
        }
        chat_room_item[1].move_to_top();
        me.move_room_to_top(chat_room_item);
      }
    });

    frappe.realtime.on('new_room_creation', function (res) {
      // if (
      //   !$('.chat-element').is(':visible') &&
      //   frappe.Chat.settings.user.enable_notifications === 1
      // ) {
      // }

      frappe.utils.play_sound('chat-notification');
      res.user = me.user;
      // res.is_admin = me.is_admin;
      // res.user_email = me.user_email;
      me.create_new_room(res);
    });

    frappe.realtime.on('private_room_creation', function (res) {
      if (
        !$('.chat-element').is(':visible') &&
        frappe.Chat.settings.user.enable_notifications === 1
      ) {
        frappe.utils.play_sound('chat-notification');
      }

      if (res.members.includes(me.user_email)) {
        if (res.room_type === 'Direct') {
          res.room_name =
            res.member_names[0]['email'] == me.user_email
              ? res.member_names[1]['name']
              : res.member_names[0]['name'];

          res.opposite_person_email =
            res.member_names[0]['email'] == me.user_email
              ? res.member_names[1]['email']
              : res.member_names[0]['email'];
        }

        res.user = me.user;
        res.is_admin = me.is_admin;
        res.user_email = me.user_email;
        me.create_new_room(res);
      }
    });
  }

  // Helper method to refresh current filter
  refresh_current_filter() {
    const query = $('.chat-search-box').val().toLowerCase();
    const showOnlyUnread = $('.unread-filter-checkbox').is(':checked');
    this.filter_rooms(query, showOnlyUnread);
  }
}
