# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Dmitriy Dobrovolskiy dima@dobrovolskiy.com

"""FreeCAD GUI commands, workbench and global menu. Imported by InitGui.py (which FreeCAD exec()s
in a namespace where module-level names are invisible to class methods - hence a real module)."""
import os

import FreeCAD as App
import FreeCADGui as Gui

_MOD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ICON_DIR = os.path.join(_MOD_DIR, "resources", "icons")


def _icon(name):
    return os.path.join(_ICON_DIR, name)


class _Cmd:
    menu = ""
    tip = ""
    icon = "freegad.svg"

    def GetResources(self):
        return {"Pixmap": _icon(self.icon), "MenuText": self.menu, "ToolTip": self.tip}

    def IsActive(self):
        return True


class FreeGAD_Chat(_Cmd):
    menu = "FreeGAD Chat"
    tip = "Open the FreeGAD chat panel (Claude inside FreeCAD)"

    def Activated(self):
        from . import ui
        ui.show_chat()


class FreeGAD_ApiKey(_Cmd):
    menu = "Set API key…"
    tip = "Set or change the API key (Anthropic or OpenAI-compatible)"
    icon = "key.svg"

    def Activated(self):
        from . import ui
        d = ui.ApiKeyDialog(Gui.getMainWindow())
        ui.run_dialog(d)


class FreeGAD_Settings(_Cmd):
    menu = "Settings…"
    tip = "Provider, model, effort, max tokens, auto-approve"
    icon = "settings.svg"

    def Activated(self):
        from . import ui
        d = ui.SettingsDialog(Gui.getMainWindow())
        ui.run_dialog(d)


class FreeGAD_Memory(_Cmd):
    menu = "Memory…"
    tip = "View and edit what FreeGAD remembers about you and this document"
    icon = "memory.svg"

    def Activated(self):
        from . import ui
        d = ui.MemoryDialog(Gui.getMainWindow())
        ui.run_dialog(d)


class FreeGAD_Reset(_Cmd):
    menu = "Reset conversation"
    tip = "Forget the current chat and re-read the document"
    icon = "reset.svg"

    def Activated(self):
        from . import ui
        ui.show_chat().on_reset()


_COMMANDS = ["FreeGAD_Chat", "FreeGAD_ApiKey", "FreeGAD_Settings", "FreeGAD_Memory", "FreeGAD_Reset"]

Gui.addCommand("FreeGAD_Chat", FreeGAD_Chat())
Gui.addCommand("FreeGAD_ApiKey", FreeGAD_ApiKey())
Gui.addCommand("FreeGAD_Settings", FreeGAD_Settings())
Gui.addCommand("FreeGAD_Memory", FreeGAD_Memory())
Gui.addCommand("FreeGAD_Reset", FreeGAD_Reset())


class FreeGADWorkbench(Gui.Workbench):
    MenuText = "FreeGAD"
    ToolTip = "Claude AI assistant for FreeCAD"
    Icon = _icon("freegad.svg")

    def Initialize(self):
        self.appendToolbar("FreeGAD", _COMMANDS)
        self.appendMenu("FreeGAD", _COMMANDS)

    def Activated(self):
        from . import ui
        ui.show_chat()

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(FreeGADWorkbench())


def _add_global_menu():
    """Put a FreeGAD menu + toolbar in the main window so the chat is reachable from ANY workbench."""
    try:
        from PySide import QtCore, QtGui, QtWidgets
        QAction = getattr(QtGui, "QAction", None) or QtWidgets.QAction   # QtGui on Qt6, QtWidgets on Qt5
    except Exception:
        return
    try:
        mw = Gui.getMainWindow()
        if mw is None:
            return
        mb = mw.menuBar()
        if mw.findChild(QtWidgets.QMenu, "FreeGADMenu"):
            return
        menu = QtWidgets.QMenu("FreeGAD", mb)
        menu.setObjectName("FreeGADMenu")
        for name in _COMMANDS:
            act = QAction(Gui.Command.get(name).getInfo().get("menuText", name), menu)
            act.triggered.connect(lambda _=False, n=name: Gui.runCommand(n))
            menu.addAction(act)
        # insert before "Help"
        before = None
        for a in mb.actions():
            if a.text().replace("&", "") == "Help":
                before = a
                break
        mb.insertMenu(before, menu) if before else mb.addMenu(menu)

        tb = QtWidgets.QToolBar("FreeGAD")
        tb.setObjectName("FreeGADGlobalToolbar")
        act = QAction(QtGui.QIcon(_icon("freegad.svg")), "FreeGAD", tb)
        act.setToolTip("Open FreeGAD chat")
        act.triggered.connect(lambda _=False: Gui.runCommand("FreeGAD_Chat"))
        tb.addAction(act)
        mw.addToolBar(tb)
    except Exception as ex:
        App.Console.PrintWarning("FreeGAD: could not add global menu: %s\n" % ex)


try:
    from PySide import QtCore as _QtCore
    _QtCore.QTimer.singleShot(1500, _add_global_menu)
except Exception:
    pass

try:
    from . import config as _config
    _config.Config.load()      # creates %APPDATA%/FreeGAD + memory dirs, migrates an installer-provided key
    from . import telemetry as _telemetry
    _telemetry.dm_init()           # anonymous usage statistics (first_run / session) -> dobrovolskiy.com
    _telemetry.flush_inflight()    # report a turn that died with the previous FreeCAD process
    _telemetry.send("session", {})
except Exception as _ex:
    App.Console.PrintWarning("FreeGAD: config init failed: %s\n" % _ex)

App.Console.PrintMessage("FreeGAD loaded. Menu: FreeGAD > FreeGAD Chat\n")
