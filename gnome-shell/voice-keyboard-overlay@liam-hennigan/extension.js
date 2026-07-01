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
  </interface>
</node>`;

const STATE_STYLES = {
    starting: {
        label: 'STARTING',
        detail: 'Opening microphone',
        bg: '#164e63',
        accent: '#22d3ee',
    },
    listening: {
        label: 'LISTENING',
        detail: 'Press Ctrl+Space again to stop',
        bg: '#7f1d1d',
        accent: '#f87171',
    },
    processing: {
        label: 'PROCESSING SPEECH',
        detail: 'Transcribing and typing',
        bg: '#78350f',
        accent: '#fbbf24',
    },
    inserted: {
        label: 'TEXT INSERTED',
        detail: '',
        bg: '#14532d',
        accent: '#4ade80',
    },
    empty: {
        label: 'NO SPEECH DETECTED',
        detail: 'Try again',
        bg: '#334155',
        accent: '#cbd5e1',
    },
    error: {
        label: 'ERROR',
        detail: '',
        bg: '#581c87',
        accent: '#d8b4fe',
    },
};

export default class VoiceKeyboardOverlayExtension extends Extension {
    enable() {
        this._actor = null;
        this._timeoutId = 0;
        this._pulseId = 0;
        this._dbus = Gio.DBusExportedObject.wrapJSObject(DBUS_XML, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._ownName = Gio.DBus.session.own_name(
            BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            null,
            () => this._hide());
    }

    disable() {
        this._hide();
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
    }

    _show(state, x, y, detail, timeoutMs) {
        this._hide();
        const style = STATE_STYLES[state] || STATE_STYLES.listening;
        const actor = this._buildActor(style, detail || style.detail);
        Main.layoutManager.addChrome(actor, {
            affectsInputRegion: false,
            affectsStruts: false,
            trackFullscreen: true,
        });
        actor.opacity = 245;
        actor.set_position(0, 0);
        actor.show();
        this._actor = actor;

        const [targetX, targetY] = this._constrainPosition(actor, x, y);
        actor.set_position(targetX, targetY);

        if (['starting', 'listening', 'processing'].includes(state))
            this._startPulse(actor);

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
                `background-color: ${style.bg}`,
                'border-radius: 12px',
                `border: 2px solid ${style.accent}`,
                'padding: 12px 16px',
                'box-shadow: 0 8px 30px rgba(0, 0, 0, 0.45)',
            ].join('; '),
        });

        this._dotBaseStyle = [
            `background-color: ${style.accent}`,
            'border-radius: 999px',
            'margin-right: 14px',
        ].join('; ');
        this._dot = new St.Widget({
            style: `${this._dotBaseStyle}; width: 18px; height: 18px; margin-top: 9px;`,
        });
        box.add_child(this._dot);

        const textBox = new St.BoxLayout({
            orientation: Clutter.Orientation.VERTICAL,
        });
        box.add_child(textBox);

        const title = new St.Label({
            text: 'VOICE KEYBOARD',
            style: 'color: rgba(255,255,255,0.75); font-size: 10pt; font-weight: bold;',
        });
        textBox.add_child(title);

        const label = new St.Label({
            text: style.label,
            style: 'color: white; font-size: 18pt; font-weight: bold;',
        });
        textBox.add_child(label);

        if (detail) {
            const detailLabel = new St.Label({
                text: detail,
                style: 'color: rgba(255,255,255,0.9); font-size: 10pt;',
            });
            textBox.add_child(detailLabel);
        }

        return box;
    }

    _startPulse(actor) {
        let large = false;
        this._pulseId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 420, () => {
            if (!this._dot || !actor)
                return GLib.SOURCE_REMOVE;
            large = !large;
            this._dot.set_style(`${this._dotBaseStyle}; ${
                large
                    ? 'width: 26px; height: 26px; margin-top: 5px;'
                    : 'width: 18px; height: 18px; margin-top: 9px;'
            }`);
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
