import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import St from 'gi://St';

import { Extension } from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const BUS_NAME = 'org.voicekeyboard.Overlay';
const OBJECT_PATH = '/org/voicekeyboard/Overlay';

const DBUS_XML = `<node>
  <interface name="org.voicekeyboard.Overlay">
    <method name="Show">
      <arg type="s" name="state" direction="in"/>
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
      <arg type="s" name="detail" direction="in"/>
      <arg type="i" name="timeoutMs" direction="in"/>
    </method>
    <method name="Hide"/>
    <method name="SetButton">
      <arg type="b" name="visible" direction="in"/>
    </method>
  </interface>
</node>`;

// Where the daemon listens (matches ipc.DEFAULT_SOCKET_PATH).
const SOCKET_PATH = GLib.build_filenamev(
    [GLib.get_user_config_dir(), 'voice-keyboard', 'socket']);
const ORB_SIZE = 46;

// ── Phosphor & Paper — the landing page's instrument aesthetic ───────────
// The overlay is a dark oscilloscope panel in every scheme (like the demo
// windows on the site): near-black glass, a thin accent hairline, a soft
// phosphor glow in the state's signal colour. Colour lives in the SIGNAL —
// the engraved label, the equalizer bars, the glow — never in a flooded
// fill. Amber (molten) only ever means "text still allowed to change".
const INSTRUMENT_BG = '#0c0c11';

const STATE_STYLES = {
    starting: {
        label: 'STARTING',
        detail: 'Opening microphone',
        accent: '#22d3ee',                  // --wave: the signal, warming up
        glow: 'rgba(34, 211, 238, 0.30)',
    },
    listening: {
        label: 'LISTENING',
        detail: 'Press again to stop',
        accent: '#e5484d',                  // --rec: the recording colour
        glow: 'rgba(229, 72, 77, 0.32)',
    },
    processing: {
        label: 'PROCESSING',
        detail: 'Transcribing',
        accent: '#fbbf24',                  // --molten: still allowed to change
        glow: 'rgba(251, 191, 36, 0.30)',
    },
    inserted: {
        label: 'INSERTED',
        detail: '',
        accent: '#51cf66',                  // --ok: cooled to ink
        glow: 'rgba(81, 207, 102, 0.28)',
    },
    empty: {
        label: 'NO SIGNAL',
        detail: 'Try again',
        accent: '#8a8a95',                  // --muted: a flat line
        glow: 'rgba(138, 138, 149, 0.18)',
    },
    error: {
        label: 'ERROR',
        detail: '',
        accent: '#a78bfa',                  // --spec-2: spectral violet
        glow: 'rgba(124, 58, 237, 0.30)',
    },
};

const ACTIVE_STATES = ['starting', 'listening', 'processing'];

// the equalizer: the landing page's signature four-bar recording motion.
const BAR_COUNT = 4;
const BAR_SLOT = 24;                        // px field the bars are anchored in
const BAR_WAVE = [6, 10, 16, 22, 16, 10];   // one cycle, staggered across bars
const BAR_STATIC = [11, 17, 13, 8];         // a frozen little skyline at rest

export default class VoiceKeyboardOverlayExtension extends Extension {
    enable() {
        this._actor = null;
        this._state = null;
        this._textBox = null;
        this._detailLabel = null;
        this._timeoutId = 0;
        this._pulseId = 0;
        this._button = null;
        this._dbus = Gio.DBusExportedObject.wrapJSObject(DBUS_XML, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._ownName = Gio.DBus.session.own_name(
            BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            null,
            () => this._hide());
        // The always-on Kai orb defaults visible; the daemon hides it via
        // SetButton(false) when [assistant].button (or the mind) is off.
        this._showButton();
    }

    disable() {
        this._hide();
        this._hideButton();
        if (this._ownName) {
            Gio.DBus.session.unown_name(this._ownName);
            this._ownName = 0;
        }
        if (this._dbus) {
            this._dbus.flush();
            this._dbus.unexport();
            this._dbus = null;
        }
    }

    Show(state, x, y, detail, timeoutMs) {
        this._show(state, x, y, detail, timeoutMs);
    }

    Hide() {
        this._hide();
    }

    SetButton(visible) {
        if (visible)
            this._showButton();
        else
            this._hideButton();
    }

    _showButton() {
        if (this._button)
            return;
        const orb = new St.Button({
            label: '⌁',
            reactive: true,
            can_focus: true,
            track_hover: true,
            style: this._orbStyle(false),
        });
        orb.connect('clicked', () => this._summon());
        // phosphor node: brightens its cyan ring + halo under the pointer.
        orb.connect('notify::hover', () => orb.set_style(this._orbStyle(orb.hover)));
        // Reactive (unlike the click-through pill) so it receives clicks.
        Main.layoutManager.addChrome(orb, {
            affectsStruts: false,
            trackFullscreen: true,
        });
        const mon = Main.layoutManager.primaryMonitor;
        const margin = 22;
        orb.set_position(
            mon.x + mon.width - ORB_SIZE - margin,
            mon.y + mon.height - ORB_SIZE - margin);
        orb.show();
        this._button = orb;
    }

    _hideButton() {
        if (this._button) {
            this._button.destroy();
            this._button = null;
        }
    }

    _orbStyle(hover) {
        return [
            `width: ${ORB_SIZE}px`,
            `height: ${ORB_SIZE}px`,
            'border-radius: 999px',
            `background-color: ${INSTRUMENT_BG}`,
            `border: 2px solid ${hover ? '#7de9f7' : '#22d3ee'}`,
            `box-shadow: 0 8px 26px rgba(34, 211, 238, ${hover ? '0.42' : '0.26'})`,
            `color: ${hover ? '#eafcff' : '#22d3ee'}`,
            'font-size: 22px',
            'font-weight: bold',
        ].join('; ');
    }

    _summon() {
        // Fire the same converse toggle the hotkey sends, straight to the
        // daemon's Unix socket (JSON, half-close to signal EOF). Fire-and-
        // forget: the turn owns its own overlay.
        try {
            const client = new Gio.SocketClient();
            const addr = new Gio.UnixSocketAddress({path: SOCKET_PATH});
            client.connect_async(addr, null, (src, res) => {
                let conn;
                try {
                    conn = src.connect_finish(res);
                } catch (e) {
                    logError(e, 'Kai orb: daemon not reachable');
                    return;
                }
                const payload = new TextEncoder().encode(
                    JSON.stringify({command: 'converse'}));
                const os = conn.get_output_stream();
                os.write_all_async(payload, GLib.PRIORITY_DEFAULT, null, (s, r) => {
                    try {
                        s.write_all_finish(r);
                        conn.get_socket().shutdown(false, true);
                    } catch (e) {
                        logError(e, 'Kai orb: write failed');
                    }
                    try {
                        conn.close(null);
                    } catch (e) {
                        // ignore
                    }
                });
            });
        } catch (e) {
            logError(e, 'Kai orb: summon failed');
        }
    }

    _clearTimers() {
        if (this._timeoutId) {
            GLib.source_remove(this._timeoutId);
            this._timeoutId = 0;
        }
        if (this._pulseId) {
            GLib.source_remove(this._pulseId);
            this._pulseId = 0;
        }
    }

    _hide() {
        this._clearTimers();
        if (this._actor) {
            this._actor.destroy();
            this._actor = null;
        }
        this._state = null;
        this._textBox = null;
        this._detailLabel = null;
    }

    _show(state, x, y, detail, timeoutMs) {
        // Same state again (the daemon's live caption updates a few times a
        // second while listening): update the detail label in place instead
        // of rebuilding, so the pill doesn't flicker and the pulse doesn't
        // restart.
        if (this._actor && this._state === state) {
            this._setDetail(detail);
            if (timeoutMs > 0) {
                if (this._timeoutId)
                    GLib.source_remove(this._timeoutId);
                this._timeoutId = GLib.timeout_add(
                    GLib.PRIORITY_DEFAULT,
                    timeoutMs,
                    () => {
                        this._timeoutId = 0;
                        this._hide();
                        return GLib.SOURCE_REMOVE;
                    });
            }
            return;
        }

        this._hide();
        this._state = state;
        const style = STATE_STYLES[state] || STATE_STYLES.listening;
        const actor = this._buildActor(style, detail || style.detail);
        // NOTE: do NOT re-add the input-region param that GNOME 50 removed
        // from addChrome's params — Params.parse throws on the unknown key
        // and the pill stops drawing entirely (see tests for the regression
        // guard). Click-through is preserved because the actors are
        // non-reactive (reactive defaults to false) on GNOME 50.
        Main.layoutManager.addChrome(actor, {
            affectsStruts: false,
            trackFullscreen: true,
        });
        actor.opacity = 245;
        actor.set_position(0, 0);
        actor.show();
        this._actor = actor;

        const [targetX, targetY] = this._constrainPosition(actor, x, y);
        actor.set_position(targetX, targetY);

        if (ACTIVE_STATES.includes(state))
            this._startBars(actor);

        if (timeoutMs > 0) {
            this._timeoutId = GLib.timeout_add(
                GLib.PRIORITY_DEFAULT,
                timeoutMs,
                () => {
                    this._timeoutId = 0;
                    this._hide();
                    return GLib.SOURCE_REMOVE;
                });
        }
    }

    _buildActor(style, detail) {
        const box = new St.BoxLayout({
            orientation: Clutter.Orientation.HORIZONTAL,
            style: [
                `background-color: ${INSTRUMENT_BG}`,
                'border-radius: 12px',
                `border: 1px solid ${style.accent}`,
                'padding: 13px 18px',
                `box-shadow: 0 10px 34px ${style.glow}`,
            ].join('; '),
        });

        // four thin phosphor bars, the state's signal colour, bottom-anchored
        // in a fixed field so they read as a live level meter.
        this._accent = style.accent;
        this._bars = new St.BoxLayout({
            orientation: Clutter.Orientation.HORIZONTAL,
            style: 'margin-right: 16px;',
        });
        this._barWidgets = [];
        for (let i = 0; i < BAR_COUNT; i++) {
            const bar = new St.Widget({style: this._barStyle(BAR_STATIC[i])});
            this._bars.add_child(bar);
            this._barWidgets.push(bar);
        }
        box.add_child(this._bars);

        const textBox = new St.BoxLayout({
            orientation: Clutter.Orientation.VERTICAL,
        });
        box.add_child(textBox);

        // the engraved-label voice: mono, tracked, uppercase (the .sigcap
        // voice from the landing page).
        const title = new St.Label({
            text: 'VOICE KEYBOARD',
            style: [
                'color: #8a8a95',
                'font-family: monospace',
                'font-size: 9pt',
                'font-weight: bold',
                'letter-spacing: 2px;',
            ].join('; '),
        });
        textBox.add_child(title);

        const label = new St.Label({
            text: style.label,
            style: [
                `color: ${style.accent}`,
                'font-family: monospace',
                'font-size: 16pt',
                'font-weight: bold',
                'letter-spacing: 1px;',
            ].join('; '),
        });
        textBox.add_child(label);

        this._textBox = textBox;
        this._detailLabel = null;
        if (detail)
            this._setDetail(detail);

        return box;
    }

    _barStyle(height) {
        const h = Math.max(3, Math.round(height));
        return [
            'width: 3px',
            `height: ${h}px`,
            `margin-top: ${BAR_SLOT - h}px`,
            'margin-right: 3px',
            'border-radius: 2px',
            `background-color: ${this._accent}`,
        ].join('; ');
    }

    _setDetail(detail) {
        if (!detail) {
            if (this._detailLabel) {
                this._detailLabel.destroy();
                this._detailLabel = null;
            }
            return;
        }
        if (this._detailLabel) {
            this._detailLabel.set_text(detail);
            return;
        }
        if (!this._textBox)
            return;
        this._detailLabel = new St.Label({
            text: detail,
            style: [
                'color: rgba(232, 232, 236, 0.72)',
                'font-family: monospace',
                'font-size: 9.5pt',
                'margin-top: 2px;',
            ].join('; '),
        });
        this._textBox.add_child(this._detailLabel);
    }

    _startBars(actor) {
        let phase = 0;
        this._pulseId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 130, () => {
            if (!this._barWidgets || !actor)
                return GLib.SOURCE_REMOVE;
            phase = (phase + 1) % BAR_WAVE.length;
            for (let i = 0; i < this._barWidgets.length; i++) {
                const h = BAR_WAVE[(phase + i * 2) % BAR_WAVE.length];
                this._barWidgets[i].set_style(this._barStyle(h));
            }
            return GLib.SOURCE_CONTINUE;
        });
    }

    _pointInMonitor(x, y, monitor) {
        return x >= monitor.x && x < monitor.x + monitor.width &&
            y >= monitor.y && y < monitor.y + monitor.height;
    }

    _pointInRect(x, y, rect, padding = 0) {
        return x >= rect.x - padding &&
            x < rect.x + rect.width + padding &&
            y >= rect.y - padding &&
            y < rect.y + rect.height + padding;
    }

    _focusedWindow() {
        try {
            const focusedWindow = global.display.focus_window ??
                (global.display.get_focus_window ? global.display.get_focus_window() : null);
            if (!focusedWindow)
                return this._topWindowOnActiveWorkspace();

            return focusedWindow;
        } catch (_) {
            return this._topWindowOnActiveWorkspace();
        }
    }

    _topWindowOnActiveWorkspace() {
        try {
            const workspace = global.workspace_manager.get_active_workspace();
            const windows = global.display.get_tab_list(Meta.TabList.NORMAL, workspace);
            return windows.find(window => {
                if (!window || window.minimized)
                    return false;
                if (window.showing_on_its_workspace && !window.showing_on_its_workspace())
                    return false;
                return true;
            }) ?? null;
        } catch (_) {
            return null;
        }
    }

    _windowRect(window) {
        if (!window)
            return null;
        try {
            const rect = window.get_frame_rect();
            if (!rect || rect.width <= 0 || rect.height <= 0)
                return null;
            return rect;
        } catch (_) {
            return null;
        }
    }

    _monitorForWindow(window, rect = null) {
        if (window) {
            try {
                const index = window.get_monitor();
                const monitor = Main.layoutManager.monitors[index];
                if (monitor)
                    return monitor;
            } catch (_) {
                // Fall through to rect matching.
            }
        }

        if (rect)
            return this._monitorForRect(rect);

        return null;
    }

    _monitorForRect(rect) {
        const centerX = rect.x + rect.width / 2;
        const centerY = rect.y + rect.height / 2;
        const monitors = Main.layoutManager.monitors;
        for (const monitor of monitors) {
            if (this._pointInMonitor(centerX, centerY, monitor))
                return monitor;
        }

        return null;
    }

    _monitorForPoint(x, y) {
        const monitors = Main.layoutManager.monitors;
        for (const monitor of monitors) {
            if (this._pointInMonitor(x, y, monitor))
                return monitor;
        }

        const focusedWindow = this._focusedWindow();
        const focusedRect = this._windowRect(focusedWindow);
        if (focusedRect) {
            const focusedMonitor = this._monitorForWindow(focusedWindow, focusedRect);
            if (focusedMonitor)
                return focusedMonitor;
        }

        return Main.layoutManager.primaryMonitor;
    }

    _anchorWithinFocusedMonitor(x, y, focusedMonitor) {
        if (x < 0 || y < 0)
            return [x, y];
        if (this._pointInMonitor(x, y, focusedMonitor))
            return [x, y];

        // Some Wayland apps expose AT-SPI coordinates relative to their
        // monitor, while GNOME Shell positions actors in global coordinates.
        if (x < focusedMonitor.width && y < focusedMonitor.height)
            return [focusedMonitor.x + x, focusedMonitor.y + y];

        return [x, y];
    }

    _fallbackPositionInMonitor(monitor, width, height) {
        return [
            Math.floor(monitor.x + Math.max((monitor.width - width) / 2, 18)),
            Math.floor(monitor.y + Math.max((monitor.height - height) / 2, 18)),
        ];
    }

    _fallbackPositionInFocusedWindow(rect, width, height) {
        const margin = 24;
        const availableWidth = Math.max(rect.width - width - margin * 2, 0);
        const availableHeight = Math.max(rect.height - height - margin * 2, 0);
        return [
            Math.floor(rect.x + margin + availableWidth / 2),
            Math.floor(rect.y + margin + availableHeight / 2),
        ];
    }

    _positionAboveAnchor(anchorX, anchorY, width, height, monitor) {
        const gap = 14;
        const margin = 18;
        let targetX = Math.floor(anchorX - width / 2);
        let targetY = Math.floor(anchorY - height - gap);

        if (targetY < monitor.y + margin)
            targetY = Math.floor(anchorY + gap);

        return [targetX, targetY];
    }

    _constrainPosition(actor, x, y) {
        const [, preferredWidth] = actor.get_preferred_width(-1);
        const [, preferredHeight] = actor.get_preferred_height(-1);
        const width = preferredWidth || actor.get_width() || 360;
        const height = preferredHeight || actor.get_height() || 86;
        const margin = 18;
        const focusedWindow = this._focusedWindow();
        const focusedRect = this._windowRect(focusedWindow);
        const focusedMonitor = this._monitorForWindow(focusedWindow, focusedRect);

        let targetX = x;
        let targetY = y;
        let usedAnchor = false;
        if (targetX < 0 || targetY < 0) {
            if (focusedRect) {
                [targetX, targetY] = this._fallbackPositionInFocusedWindow(
                    focusedRect,
                    width,
                    height,
                );
            } else {
                [targetX, targetY] = this._fallbackPositionInMonitor(
                    Main.layoutManager.primaryMonitor,
                    width,
                    height,
                );
            }
        } else if (focusedMonitor) {
            [targetX, targetY] = this._anchorWithinFocusedMonitor(
                targetX,
                targetY,
                focusedMonitor,
            );
            usedAnchor = true;
        } else {
            usedAnchor = true;
        }

        if (usedAnchor && focusedRect && !this._pointInRect(targetX, targetY, focusedRect, 96)) {
            [targetX, targetY] = this._fallbackPositionInFocusedWindow(
                focusedRect,
                width,
                height,
            );
            usedAnchor = false;
        }

        const monitor = this._monitorForPoint(targetX, targetY);
        if (usedAnchor)
            [targetX, targetY] = this._positionAboveAnchor(
                targetX,
                targetY,
                width,
                height,
                monitor,
            );

        targetX = Math.max(monitor.x + margin,
            Math.min(targetX, monitor.x + monitor.width - width - margin));
        targetY = Math.max(monitor.y + margin,
            Math.min(targetY, monitor.y + monitor.height - height - margin));

        return [targetX, targetY];
    }
}
