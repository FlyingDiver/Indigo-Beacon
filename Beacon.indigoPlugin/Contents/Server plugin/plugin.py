#! /usr/bin/env python
# -*- coding: utf-8 -*-

import cgi
import fnmatch
import threading
from urllib.parse import parse_qs

import simplejson as json
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
from SocketServer import ThreadingMixIn


def updateVar(name, value):
    if name not in indigo.variables:
        indigo.variable.create(name, value=value)
    else:
        indigo.variable.updateValue(name, value)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in a separate thread."""


class HttpHandler(BaseHTTPRequestHandler):
    def __init__(self, plugin, *args):
        self.plugin = plugin
        self.plugin.debugLog(f"New HttpHandler thread: {threading.current_thread().name}, total threads: {threading.active_count()}")
        BaseHTTPRequestHandler.__init__(self, *args)

    def deviceUpdate(self, device, deviceAddress, event):
        self.plugin.debugLog("deviceUpdate called")

        if self.plugin.createVar:
            updateVar("Beacon_deviceID", str(device.id))
            updateVar("Beacon_name", deviceAddress.split('@@')[0])
            updateVar("Beacon_location", deviceAddress.split('@@')[1])

        if event == "LocationEnter" or event == "enter" or event == "1" or event == self.plugin.customEnter:
            indigo.server.log(f"Enter location notification received from sender/location {deviceAddress}")
            device.updateStateOnServer("onOffState", True)
            device.updateStateImageOnServer(indigo.kStateImageSel.MotionSensorTripped)
            self.triggerEvent("statePresent", deviceAddress)
        elif event == "LocationExit" or event == "exit" or event == "0" or event == self.plugin.customExit:
            indigo.server.log(f"Exit location notification received from sender/location {deviceAddress}")
            device.updateStateOnServer("onOffState", False)
            device.updateStateImageOnServer(indigo.kStateImageSel.MotionSensor)
            self.triggerEvent("stateAbsent", deviceAddress)
        elif event == "LocationTest" or event == "test":
            indigo.server.log(f"Test location notification received from sender/location {deviceAddress}")
            if self.plugin.testTrigger:
                indigo.server.log(f"Trigger action on test is enabled, triggeraction: {self.plugin.testTrigger}")
                if self.plugin.testTriggeraction == "enter":
                    device.updateStateOnServer("onOffState", True)
                    device.updateStateImageOnServer(indigo.kStateImageSel.MotionSensorTripped)
                elif self.plugin.testTriggeraction == "exit":
                    device.updateStateOnServer("onOffState", False)
                    device.updateStateImageOnServer(indigo.kStateImageSel.MotionSensor)
                elif self.plugin.testTriggeraction == "toggle":
                    device.updateStateOnServer("onOffState", not device.onState)
                    if device.onState:
                        device.updateStateImageOnServer(indigo.kStateImageSel.MotionSensorTripped)
                    else:
                        device.updateStateImageOnServer(indigo.kStateImageSel.MotionSensor)
        self.triggerEvent("stateChange", deviceAddress)

    def triggerEvent(self, eventType, deviceAddress):
        self.plugin.debugLog("triggerEvent called")
        for trigger in self.plugin.events[eventType]:
            if self.plugin.events[eventType][trigger].pluginProps["manualAddress"]:
                indigo.trigger.execute(trigger)
            elif fnmatch.fnmatch(deviceAddress.lower(), self.plugin.events[eventType][trigger].pluginProps["deviceAddress"].lower()):
                indigo.trigger.execute(trigger)

    def deviceCreate(self, sender, location):
        self.plugin.debugLog("deviceCreate called")
        deviceName = sender + "@@" + location
        device = indigo.device.create(address=deviceName, deviceTypeId="beacon", name=deviceName, protocol=indigo.kProtocol.Plugin)
        self.plugin.deviceList[device.id] = {'ref': device, 'name': device.name, 'address': device.address.lower()}
        self.plugin.debugLog(f"Created new device, {deviceName}")
        device.updateStateOnServer("onOffState", False)
        device.updateStateImageOnServer(indigo.kStateImageSel.MotionSensor)
        return device.id

    def parseResult(self, sender, location, event):
        self.plugin.debugLog(u"parseResult called")
        deviceAddress = sender.lower() + "@@" + location.lower()
        foundDevice = False
        if self.plugin.deviceList:
            for b in self.plugin.deviceList:
                if self.plugin.deviceList[b]['address'] == deviceAddress:
                    self.plugin.debugLog(f"Found userLocation device: {self.plugin.deviceList[b]['name']}")
                    self.deviceUpdate(self.plugin.deviceList[b]['ref'], deviceAddress, event)
                    foundDevice = True
        if not foundDevice:
            self.plugin.debugLog(u"No device found")
            indigo.server.log(f"Received {event} from {deviceAddress} but no corresponding device exists", isError=True)
            if self.plugin.createDevice:
                newdev = self.deviceCreate(sender, location)
                self.deviceUpdate(self.plugin.deviceList[newdev]['ref'], deviceAddress, event)

    def do_POST(self):
        #       global rootnode
        foundDevice = False
        self.plugin.debugLog(u"Received HTTP POST")
        self.plugin.debugLog(u"Sending HTTP 200 response")
        self.send_response(200)
        self.end_headers()

        try:
            ctype, pdict = cgi.parse_header(self.headers.getheader('content-type'))
            uagent = str(self.headers.getheader('user-agent'))
            self.plugin.debugLog(f"User-agent: {uagent}, Content-type: {ctype}")
            data = self.rfile.read(int(self.headers['Content-Length']))
            data = data.decode('utf-8')
            self.plugin.debugLog(f"Data (UTF-8 decoded):  {data}")
            # Custom
            if self.plugin.custom and (ctype == 'application/x-www-form-urlencoded'):
                pdata = parse_qs(data)
                p = {}
                for key, value in pdata.iteritems():
                    p.update({key: value[0]})
                if all((name in p) for name in (self.plugin.customSender, self.plugin.customLocation, self.plugin.customAction)):
                    self.plugin.debugLog(u"Recognised Custom")
                    if (p[self.plugin.customAction] == self.plugin.customEnter) or (p[self.plugin.customAction] == self.plugin.customExit):
                        self.parseResult(p[self.plugin.customSender], p[self.plugin.customLocation], p[self.plugin.customAction])
                    else:
                        indigo.server.log("Received Custom data, but value of Action parameter wasn't recognised", isError=True)
                    return
            # Locative
            if ('Geofancy' in uagent) or ('Locative' in uagent):
                self.plugin.debugLog(u"Recognised Locative")
                if self.plugin.geofancy:
                    if ctype == 'application/x-www-form-urlencoded':
                        pdata = parse_qs(data)
                        p = {}
                        for key, value in pdata.iteritems():
                            p.update({key: value[0]})
                        if all((name in p) for name in ('device', 'id', 'trigger')):
                            self.parseResult(p["device"], p["id"], p["trigger"])
                        else:
                            indigo.server.log("Received Locative data, but one or more parameters are missing", isError=True)
                    else:
                        indigo.server.log(f"Recognised Locative, but received data was wrong content-type: {ctype}", isError=True)
                else:
                    indigo.server.log("Received Locative data, but Locative is disabled in plugin config")
            # Geofency
            elif 'Geofency' in uagent:
                self.plugin.debugLog(u"Recognised Geofency")
                if self.plugin.geofency:
                    if ctype == 'application/json':
                        p = json.loads(data)
                        if all((name in p) for name in ('name', 'entry', 'device')):
                            self.parseResult(p["device"], p["name"], p["entry"])
                        else:
                            indigo.server.log(u"Received Geofency data, but one or more parameters are missing", isError=True)
                    else:
                        indigo.server.log(f"Recognised Geofency, but received data was wrong content-type: {ctype}", isError=True)
                else:
                    indigo.server.log(u"Received Geofency data, but Geofency is disabled in plugin config")
            # Beecon
            elif 'Beecon' in uagent:
                self.plugin.debugLog(u"Recognised Beecon")
                if self.plugin.beecon:
                    pdata = parse_qs(data)
                    p = {}
                    for key, value in pdata.iteritems():
                        p.update({key: value[0]})
                    if all((name in p) for name in ('region', 'action')):
                        self.parseResult("Beecon", p["region"], p["action"])
                    else:
                        indigo.server.log(u"Received Beecon data, but one or more parameters are missing", isError=True)
                else:
                    indigo.server.log(u"Received Beecon data, but Beecon is disabled in plugin config")
            # Geohopper
            elif ctype == 'application/json':
                self.plugin.debugLog(u"Received JSON data (possible Geohopper)")
                if self.plugin.geohopper:
                    p = json.loads(data)
                    if all((name in p) for name in ('sender', 'location', 'event')):
                        self.parseResult(p["sender"], p["location"], p["event"])
                    else:
                        indigo.server.log(u"Received Geohopper data, but one or more parameters are missing", isError=True)
                else:
                    indigo.server.log(u"Received Geohopper data, but Geohopper is disabled in plugin config")
            else:
                indigo.server.log(f"Didn't recognise received data. (User-agent: {uagent}, Content-type: {ctype})", isError=True)
        except Exception as e:
            indigo.server.log(f"Exception: {e}", isError=True)
            pass


class Plugin(indigo.PluginBase):
    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)
        self.server = None
        self.testTriggeraction = None
        self.testTrigger = None
        self.customExit = None
        self.customEnter = None
        self.customAction = None
        self.customLocation = None
        self.customSender = None
        self.custom = None
        self.createVar = None
        self.geofency = None
        self.geohopper = None
        self.geofancy = None
        self.beecon = None
        self.listenPort = None
        self.createDevice = None
        self.debug = None
        self.deviceList = {}

        self.events = dict()
        self.events["stateChange"] = dict()
        self.events["statePresent"] = dict()
        self.events["stateAbsent"] = dict()
        self.myThread = threading.Thread(target=self.listenHTTP, args=())
        self.myThread.daemon = True
        self.myThread.start()

    def startup(self):
        self.loadPluginPrefs()
        self.debugLog("Startup called")

    def deviceCreated(self, device):
        self.debugLog(device.name + f"Created device of type \"{device.deviceTypeId}\"")
        self.deviceList[device.id] = {'ref': device, 'name': device.name, 'address': device.address.lower()}

    def deviceStartComm(self, device):
        self.debugLog(device.name + ": Starting device")
        if device.deviceTypeId == u'userLocation':
            indigo.server.log(f"Device {device.name} needs to be deleted and recreated.", isError=True)
        else:
            self.deviceList[device.id] = {'ref': device, 'name': device.name, 'address': device.address.lower()}

    def deviceStopComm(self, device):
        self.debugLog(device.name + ": Stopping device")
        if device.deviceTypeId == u'beacon':
            del self.deviceList[device.id]

    def shutdown(self):
        self.debugLog(u"Shutdown called")

    def triggerStartProcessing(self, trigger):
        self.debugLog(f"Start processing trigger {trigger.name}")
        self.events[trigger.pluginTypeId][trigger.id] = trigger

    def triggerStopProcessing(self, trigger):
        self.debugLog(f"Stop processing trigger {trigger.name}")
        if trigger.pluginTypeId in self.events:
            if trigger.id in self.events[trigger.pluginTypeId]:
                del self.events[trigger.pluginTypeId][trigger.id]

    def actionControlSensor(self, action, device):
        self.debugLog(f"Manual sensor state change request: {device.name}")
        if device.pluginProps['AllowOnStateChange']:
            if action.sensorAction == indigo.kSensorAction.TurnOn:
                device.updateStateOnServer("onOffState", True)
                device.updateStateImageOnServer(indigo.kStateImageSel.MotionSensorTripped)
            elif action.sensorAction == indigo.kSensorAction.TurnOff:
                device.updateStateOnServer("onOffState", False)
                device.updateStateImageOnServer(indigo.kStateImageSel.MotionSensor)
            elif action.sensorAction == indigo.kSensorAction.Toggle:
                device.updateStateOnServer("onOffState", not device.onState)
                if device.onState:
                    device.updateStateImageOnServer(indigo.kStateImageSel.MotionSensorTripped)
                else:
                    device.updateStateImageOnServer(indigo.kStateImageSel.MotionSensor)
        else:
            self.debugLog("ignored request (sensor is read-only)")

    def validatePrefsConfigUi(self, valuesDict):
        self.debugLog(u"validating Prefs called")
        port = int(valuesDict['listenPort'])
        if port <= 0 or port > 65535:
            errorMsgDict = indigo.Dict()
            errorMsgDict['port'] = "Port number needs to be a valid TCP port (1-65535)."
            return False, valuesDict, errorMsgDict
        if valuesDict['custom']:
            if valuesDict['customSender'] == "":
                errorMsgDict = indigo.Dict()
                errorMsgDict['customSender'] = u"Sender field can't be empty"
                return False, valuesDict, errorMsgDict
            if valuesDict['customLocation'] == "":
                errorMsgDict = indigo.Dict()
                errorMsgDict['customLocation'] = "Location field can't be empty"
                return False, valuesDict, errorMsgDict
            if valuesDict['customAction'] == "":
                errorMsgDict = indigo.Dict()
                errorMsgDict['customAction'] = "Action field can't be empty"
                return False, valuesDict, errorMsgDict
            if valuesDict['customEnter'] == "":
                errorMsgDict = indigo.Dict()
                errorMsgDict['customEnter'] = "Enter field can't be empty"
                return False, valuesDict, errorMsgDict
            if valuesDict['customExit'] == "":
                errorMsgDict = indigo.Dict()
                errorMsgDict['customExit'] = "Exit field can't be empty"
                return False, valuesDict, errorMsgDict
            if valuesDict['customEnter'] == valuesDict['customExit']:
                errorMsgDict = indigo.Dict()
                errorMsgDict['customExit'] = "Enter and Exit fields can't have same value"
                return False, valuesDict, errorMsgDict
            if valuesDict['customSender'] == valuesDict['customLocation']:
                errorMsgDict = indigo.Dict()
                errorMsgDict['customLocation'] = "Sender and Location fields can't have same value"
                return False, valuesDict, errorMsgDict
        return True, valuesDict

    def closedPrefsConfigUi(self, valuesDict, UserCancelled):
        if UserCancelled is False:
            indigo.server.log("Preferences were updated.")
            if not (self.listenPort == int(self.pluginPrefs['listenPort'])):
                indigo.server.log("New listen port configured, reload plugin for change to take effect", isError=True)
            self.loadPluginPrefs()

    def loadPluginPrefs(self):
        self.debugLog(u"loadpluginPrefs called")
        self.debug = self.pluginPrefs.get('debugEnabled', False)
        self.createDevice = self.pluginPrefs.get('createDevice', True)
        self.listenPort = int(self.pluginPrefs.get('listenPort', 6192))
        self.beecon = self.pluginPrefs.get('beecon', True)
        self.geofancy = self.pluginPrefs.get('geofancy', True)
        self.geohopper = self.pluginPrefs.get('geohopper', True)
        self.geofency = self.pluginPrefs.get('geofency', True)
        self.createVar = self.pluginPrefs.get('createVar', False)
        self.custom = self.pluginPrefs.get('custom', False)
        self.customSender = self.pluginPrefs.get('customSender', 'sender')
        self.customLocation = self.pluginPrefs.get('customLocation', 'location')
        self.customAction = self.pluginPrefs.get('customAction', 'action')
        self.customEnter = self.pluginPrefs.get('customEnter', 'enter')
        self.customExit = self.pluginPrefs.get('customExit', 'exit')
        self.testTrigger = self.pluginPrefs.get('testTrigger', False)
        self.testTriggeraction = self.pluginPrefs.get('testTriggeraction', 'toggle')

    def listenHTTP(self):
        self.debugLog(u"Starting HTTP listener thread")
        indigo.server.log(f"Listening on TCP port {self.listenPort}")
        self.server = ThreadedHTTPServer(('', self.listenPort), lambda *args: HttpHandler(self, *args))
        self.server.serve_forever()

