# coding=utf-8
import subprocess
import logging
import sys
import os
import re

from time import sleep
from pipes import quote #CHECK if used

class Nmcli:

    def __init__(self, mocking = False):

        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger("octoprint.plugins.networkmanager.nmcli")

        self.mocking = mocking

        try: 
            self.check_nmcli_version()
        except ValueError as err:
            self.logger.error("Nmcli incorrect version: {version}. Must be higher than 0.9.9.0".format(version=err.args[0]))
            raise Exception

        self.ip_regex = re.compile('(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)')
 
    def _send_command(self, command):
        """
        Sends command to ncmli with subprocess. 
        Returns (0, output) of the command if succeeded, returns the exit code and output when errors
        """

        self._log_command(command)

        if self.mocking:
            return 1, None

        command[:0] = ["nmcli"]
        try:
            result = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            output , error = result.communicate()

            # Error detected, return exit code and output + error
            # Output is returned because nmcli reports error states in output and not in error ><
            if result.returncode != 0:
                self.logger.warn("Error while trying execute command {command}: output: {output}".format(command=command, output=output, error=error))
                #raise subprocess.CalledProcessError(result.returncode, command, output)
                #An error occured, return the return code and one string of the 
                return result.returncode, output

            return result.returncode, output
        except OSError as e:
            self.logger.warn("OSError: {error}, file: {filename}, error: {message}".format(error=e.errno, filename=e.filename, message=e.strerror))



    def scan_wifi(self, force=False):
        """
        Scans wifi acces points and returns list of cells

        TODO: Add rescan option
        """

        #Force rescan if required
        if force:
            self.rescan_wifi()

        command = ["-t", "-f", "SSID, SIGNAL, SECURITY", "dev", "wifi", "list"]
        # Keys to map the out put to, same as fields describes in the command
        keys = ["ssid", "signal", "security"]

        # Parse command
        parse = self._sanatize_parse(self._send_command(command))

        if self.mocking:
            result = []
            for i in range(0,20):
                result.append(dict(ssid="Leapfrog%d" % (i+1), signal=(20-i)*5, security=(i%2==0)))
            return result

        # Map output to dict with keys[]
        cells = self._map_parse(parse, keys)

        # Convert signal to int
        for cell in cells:
            cell["signal"] = int(cell["signal"])

        # Filter duplicates and return keep only highest signal entry
        cells = self._filter_cells(cells)
        return cells

    def rescan_wifi(self):
        """
        Rescans the wifi APS
        """
        command = ["dev", "wifi", "rescan"]

        return self._send_command(command)

    def get_status(self):
        """
        Return status of connections.
        Returns:
            ethernet:
                connection_uuid: string
                connected: bool
                ip: string
            wifi:
                connection_uuid: string
                connected: bool
                ip: string
                ssid: string
        """
        if self.mocking:
            return { 
                "ethernet" : { 
                    "connection_uuid" : "1234", 
                    "connected" : True, 
                    "ip" : "127.0.0.1" },
                "wifi" : {
                    "connection_uuid" : "5678", 
                    "connected" : True, 
                    "ssid" : "Leapfrog2",
                    "ip" : "127.0.0.2"
                    } 
                }

        result = {}

        interfaces = self.get_interfaces()

        for interface in interfaces:
            result[interface] = {}
            result[interface]["connection_uuid"] = interfaces[interface]["connection_uuid"]
            result[interface]["connected"] = self.is_device_active(interfaces[interface]["device"])
            
            if self.is_device_active(interfaces[interface]):
                result[interface]["ip"] = self._get_interface_ip(interfaces[interface]["device"])

            if interface == "wifi":
                details = self.get_configured_connection_details(interfaces[interface]["connection_uuid"])
                result[interface]["ssid"] = details["802-11-wireless.ssid"]
            
        return result

    def get_configured_connections(self):
        """
        Get all configured connections for wireless and wired configurations
        """
        command = ["-t", "-f", "name, uuid, type", "c"]
        keys =["name", "uuid", "type"]

        parse = self._sanatize_parse(self._send_command(command))

        configured_connections = self._map_parse(parse, keys)

        # Sanatize the connection name a bit
        for connection in configured_connections:
            if "wireless" in connection["type"]:
                connection["type"] = "Wireless"
            if "ethernet" in connection["type"]:
                connection["type"] = "Wired"

        return configured_connections

    def delete_configured_connection(self, uuid):
        """
        Deletes a configured connection. Takes uuid as input
        """

        command = ["con", "delete", "uuid", uuid]
        
        result = self._send_command(command)

        if result[0]:
            self.logger.warn("An error occurred deleting a connection") 
            return False
        else:
            self.logger.info("Connection with uuid: {uuid} deleted".format(uuid=uuid))
            return True

    def get_configured_connection_details(self, uuid):
        command = ["-t", "con", "show", uuid ]
        
        if self.mocking:
            if uuid == "1234":
                # Ethernet
                details = {
                "connection.type": "802-3-ethernet",
                "802-3-ethernet.mac-address": "12:34:56:WI:RE:D0:00",
                "ipv4.method" : "manual",
                "ipv4.addresses" : "ip = 127.0.0.1/24",
                "ipv4.routes" : "dst = 192.168.0.1/24",
                "ipv4.dns" : "1.1.1.1 2.2.2.2"
                }
            elif uuid == "5678":
                # Wifi
                details = {
                    "connection.type": "802-11-wireless",
                    "802-11-wireless.ssid": "Leapfrog2",
                    "802-11-wireless.mac-address": "12:34:56:WI:RE:LE:SS",
                    "ipv4.method" : "auto",
                    "ipv4.addresses" : "ip = 127.0.0.2/24",
                    "ipv4.routes" : "dst = 192.168.0.1/24",
                    "ipv4.dns" : "8.8.8.8 4.4.4.4"
                    }
        else:
            details = self._sanatize_parse_key_value(self._send_command(command))

        result = {
            "uuid": uuid,
            "name": self._get_connection_name(details),
            "macaddress": self._get_mac_address(details),
            "isWireless": "wireless" in details["connection.type"],
            "psk": "",
            "ipv4": {
                "method": details["ipv4.method"],
                "ip": self._get_ipv4_address(details["ipv4.addresses"]),
                "gateway": self._get_gateway_ipv4_address(details["ipv4.routes"]),
                "dns": details["ipv4.dns"].split()
                }
            }

        return result

    def set_configured_connection_details(self, uuid, connection_details):
        command = ["-t", "con", "modify", uuid ]
        new_settings = {}

        if connection_details["isWireless"] and "psk" in connection_details and connection_details["psk"]:
            new_settings["802-11-wireless-security.psk"] = connection_details["psk"]

        new_settings["ipv4.method"] = connection_details["ipv4"]["method"]

        if new_settings["ipv4.method"] == "manual":
            new_settings["ipv4.ip"] = connection_details["ipv4"]["ip"]
            new_settings["ipv4.routes"] = connection_details["ipv4"]["gateway"]
            new_settings["ipv4.dns"] = " ".join(connection_details["ipv4"]["dns"])

        for setting, value in new_settings.iteritems():
            command.append(setting)
            command.append("\"" + value + "\"")

        exitcode, _ = self._send_command(command)

        return exitcode == 0

    def clear_configured_connection(self, ssid):
        """
        Delete all wifi configurations with ssid in name. Might be needed after multiple of the same connetions are created
        """
        for connection in self.get_configured_connections():
            self.logger.info("Deleting connection {0}".format(connection["name"])) 
            if ssid in connection["name"]:
                self.delete_configured_connection(connection["uuid"])


    def disconnect_interface(self, interface):
        """
        Disconnect either 'wifi' or 'ethernet'. Uses disconnect_device and is_device_active to disconnect an interface.__init__.py
        """
        interfaces = self.get_interfaces()

        if interface in interfaces:
            device = interfaces[interface]["device"]
            return self._disconnect_device(device)
        else:
            self.logger.error("Could not find interface {0}".format(interface))

    def _disconnect_device(self, device):
        """ 
        Disconnect wifi selected. This uses 'nmcli dev disconnect interface' since thats is the recommended method. 
        Using 'nmcli con down SSID' will bring the connection down but will not make it auto connect on the interface any more.
        """

        if self.is_device_active(device):
            command = ["dev", "disconnect", device]
            
            return self._send_command(command)
        return (1, "Device not active") 

    def is_wifi_configured(self):
        """
        Checks if wifi is configured on the machine
        """

        command = ["-t", "-f", "type", "dev"]
        devices = self._sanatize_parse(self._send_command(command))

        for device in devices:
            if "wifi" in device:
                return True
        return False

    def is_device_active(self, device):
        """
        Checks if device(wlan0, eth0, etc) is active
        Returns True if active, falls if not active
        """
        command = ["-t", "-f", "device, state", "device", "status"]
        devices = self._sanatize_parse(self._send_command(command))

        if self.mocking:
            return True

        if devices:
            for elem in devices:
                if device in elem:
                    return elem[1] == "connected"

        # We didnt find any device matching, return False also
        return False 

    def get_active_connections(self):
        """
        Get active connections

        returns a dict of active connections with key:value, interace: cell
        """
        command = ["-t", "-f", "NAME, DEVICE, TYPE", "c", "show", "--active"]
        keys = ["name", "device", "type"]

        parse = self._sanatize_parse(self._send_command(command))

        connections = self._map_parse(parse, keys)

        return connections


    def connect_wifi(self, ssid, psk=None):
        """
        Connect to wifi AP. Should check if configuration of SSID already exists and use that or create a new entry
        """

        #C Check if connection alredy is configured

        configured_connections = self.get_configured_connections()
        for connection in configured_connections:
            if ssid in connection.values():
                # The ssid we are trying to connect to already has a configuration file. 
                # Delete it and all it's partial configuration files before trying to set up a new connection
                self.clear_configured_connection(ssid)

        # The connection does not seem to be configured yet, so lets add it
        command = ["dev", "wifi", "connect", ssid]
        if psk:
            command.extend(["password", psk])

        self.logger.info("Trying to create new connection for {0}".format(ssid))
        
        return self._send_command(command)


    def reset_wifi(self):
        """
        Resets the wifi by turning it on and off with sleep of 5 seconds
        """
        self._send_command(["radio", "wifi", "off"])
        sleep(5)
        self._send_command(["radio", "wifi", "on"])
        self.logger.info("Wifi reset")

    def get_interfaces(self):
        """
        Return list of interfaces
        For example {'ethernet': { 'device': 'eth0', 'connection_uuid' : '1234-ab-..' }, 'wifi': { 'device': 'wlan0', 'connection_uuid' : '1234-ab-..' }}
        """
        command = ["-t","-f","type, device, con-uuid", "dev"]

        parse = self._sanatize_parse(self._send_command(command))

        if self.mocking:
            return {'ethernet': { 'device': 'eth0', 'connection_uuid' : '1234' }, 'wifi': { 'device': 'wlan0', 'connection_uuid' : '5678' }}

        if parse:
            interfaces = dict((x[0], { "device": x[1], "connection_uuid": x[2] }) for x in parse)
        else:
            interfaces = dict()

        return interfaces

    def _get_interface_ip(self, device):
        """
        Get the ip of the connection
        """

        command = ["-t", "-f", "IP4.ADDRESS", "d", "show", device] 
        parse = self._sanatize_parse(self._send_command(command))

        ip = None
        for elem in parse[0]:
            match = self.ip_regex.search(elem)
            if match:
                ip = match.group()

        return ip

    def _map_parse(self, parse, keys):
        cells = []
        for elem in parse:
            cell = dict(zip(keys, elem))
            cells.append(cell)
        return cells

    def _sanatize_parse(self, output):
        """
        Sanatizes the parse. using the -t command of nmli, ':' is used to split
        """
        #Check if command executed correctly[returncode 0], otherwise return nothing
        if not output[0]:
            parse = output[1].splitlines()
            parse_split = []
            for line in parse:
                line = line.split(":")
                parse_split.append(line)
            return parse_split
    
    def _sanatize_parse_key_value(self, output):
        """
        Sanatizes the parse. using the -t command of nmli, ':' is used to split. Returns key-value pairs
        """
        #Check if command executed correctly[returncode 0], otherwise return nothing
        if not output[0]:
            parse = output[1].splitlines()
            parse_split = {}
            for line in parse:
                line = line.split(":")
                if len(line) == 2:
                    parse_split[line[0]] = line[1]
            return parse_split

    def _filter_cells(self, cells):
        """
        Filter cells dictionary to remove duplicates and only keep the entry with the highest signal value
        """
        filtered = {}
        for cell in cells:
            ssid = cell["ssid"]
            if ssid in filtered:
                if cell["signal"] > filtered[ssid]["signal"]:
                    filtered[ssid] = cell
            else:
                filtered[ssid] = cell 

        return filtered.values()

    def check_nmcli_version(self):
        """
        Check the nmcli version value as this wrapper is only compatible with 0.9.9.0 and up.
        """
        exit_code, response = self._send_command(["--version"])
        
        if exit_code == 0:
            parts = response.split()
            ver = parts[-1]
            compare = self.vercmp(ver, "0.9.9.0")
            if compare >= 0:
                return True
            else: 
                raise ValueError(ver)
                return False
        else:
            return False

    def _get_connection_name(self, connection_details):
        if "802-11-wireless.ssid" in connection_details:
            return connection_details["802-11-wireless.ssid"]
        else:
            return "Wired"

    def _get_ipv4_address(self, ip_details):
        look_for_start = "ip = "
        look_for_end = "/"

        start_idx = ip_details.find(look_for_start)
        end_idx = ip_details.find(look_for_end, start_idx+len(look_for_start))

        if start_idx > -1 and end_idx > -1:
            return ip_details[start_idx+len(look_for_start):end_idx]

    def _get_gateway_ipv4_address(self, ip_details):
        look_for_start = "dst = "
        look_for_end = "/"

        start_idx = ip_details.find(look_for_start)
        end_idx = ip_details.find(look_for_end, start_idx+len(look_for_start))

        if start_idx > -1 and end_idx > -1:
            return ip_details[start_idx+len(look_for_start):end_idx]

    def _get_mac_address(self, connection_details):
        look_for = ["802-11-wireless.mac-address", "802-3-ethernet.mac-address"]
        
        for find in look_for:
            if find in connection_details:
                return connection_details[find]

    def _log_command(self, command):
        command_str = " ".join(command)
        self.logger.debug("NMCLI Sending command: {0}".format(command_str))

    def vercmp(self, actual, test):
        def normalize(v):
            return [int(x) for x in re.sub(r'(\.0+)*$', '', v).split(".")]
        return cmp(normalize(actual), normalize(test))
