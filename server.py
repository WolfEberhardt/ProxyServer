import socket
import select
import threading
import os
import logging
import colorama
from colorama import Fore
import atexit

# Konfiguration der globalen Variablen
class Vars:
    running = True

# Klasse zur Handhabung der Konfiguration
class Config:
    def __init__(self) -> None:
        # Lese Konfigurationsdatei
        config_path = os.path.join(os.path.dirname(__file__), ".config")
        with open(config_path, "r", encoding="utf-8") as config_file:
            self.config_data = config_file.read().splitlines()

        self.settings = {}
        settings = self.__extract()

        # Einstellungen aus der Konfiguration laden
        self.auto_setup = settings['auto_setup']
        self.host = settings['host']
        if self.host == "onlinehost":
            self.host = socket.gethostbyname(socket.gethostname())  # Lokale IP ermitteln
        self.port = settings['port']
        self.logging_enabled = settings['logging']
        self.colorfull = settings['colorfull']
        self.exclude_domains = settings['exclude_domains'].split(',') if "," in settings['exclude_domains'] else [settings['exclude_domains']]
        self.max_users = settings['max_users']

    # Extrahiere die Konfigurationswerte
    def __extract(self):
        for line in self.config_data:
            line = line.strip()
            if line.startswith('#') or not line:
                continue

            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip().strip('"')

            if value.lower() == 'true':
                self.settings[key] = True
            elif value.lower() == 'false':
                self.settings[key] = False
            elif value.isdigit():
                self.settings[key] = int(value)
            elif value == 'None':
                self.settings[key] = None
            else:
                self.settings[key] = value

        return self.settings

# Klasse zur Filterung von Anfragen
class Filters:
    def __init__(self, config) -> None:
        self.config_settings = config
        with open(os.path.dirname(__file__) + "/blocked.txt", "r", encoding="utf-8") as file:
            self.BLOCK_DOMAINS = file.read().splitlines()

        # Domains aus der zweiten Datei hinzufügen und als Set speichern, um Duplikate zu vermeiden
        with open(os.path.dirname(__file__) + "/blocked_II.txt", "r", encoding="utf-8") as file:
            self.BLOCK_DOMAINS.extend(file.read().splitlines())

        # Um Duplikate zu entfernen, die sich aus den beiden Dateien ergeben können
        self.BLOCK_DOMAINS = set(self.BLOCK_DOMAINS)

    # Hauptlogik zur Verarbeitung der Anfragen durch Filter
    def through_filters(self, client_socket, addr):
        try:
            request = client_socket.recv(4096)
            if not request:
                logging.debug("Keine Antwort vom Client")
                return

            request_text = request.decode()#errors='ignore')
            first_line = request_text.split('\n')[0]
            method = first_line.split()[0]  # GET, CONNECT, etc.
            url = first_line.split()[1]
            target_address = self.get_host_header(request) or self.extract_host_from_url(url)

            target_address = target_address.split(':')[0]  # Entferne Port, falls vorhanden

            if self.is_address_blocked(target_address, addr):
                client_socket.close()
                return

            if method == 'GET':
                self.handle_get_request(client_socket, request, target_address)
            elif method == 'CONNECT':
                self.handle_connect_request(client_socket, target_address)

        except Exception as e:
            logging.error(f"Ein Fehler ist aufgetreten: {e}")
        finally:
            client_socket.close()

    # Bearbeitung von GET-Anfragen
    def handle_get_request(self, client_socket, request, target_address):
        target_port = 80
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as target_socket:
            target_socket.connect((target_address, target_port))
            target_socket.sendall(request)

            while Vars.running:
                response = target_socket.recv(16384)
                if not response:
                    break
                client_socket.sendall(response)

    # Bearbeitung von CONNECT-Anfragen (z.B. HTTPS)
    def handle_connect_request(self, client_socket, target_address):
        client_socket.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        target_port = 443
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as target_socket:
            target_socket.connect((target_address, target_port))

            while Vars.running:
                rlist, _, _ = select.select([client_socket, target_socket], [], [])
                if client_socket in rlist:
                    data = client_socket.recv(4096)
                    if not data:
                        break
                    target_socket.sendall(data)
                if target_socket in rlist:
                    data = target_socket.recv(4096)
                    if not data:
                        break
                    client_socket.sendall(data)

    # Überprüft, ob die Adresse blockiert ist
    def is_address_blocked(self, address, addr):
        print(address)
        try:
            if address in self.BLOCK_DOMAINS:
                logging.debug(f"{Fore.RED} {address} send to Void!")
                return True
            else:
                logging.debug(f"{Fore.GREEN} {address} Connection accessed.")
                return False

        except Exception as e:
            logging.error(f"Fehler beim Lesen der Blockliste: {e}")
            return True

    # Extrahiere die Host-Adresse aus dem HTTP-Header
    def get_host_header(self, request):
        headers = request.decode(errors='ignore').split('\r\n')
        for header in headers:
            if header.lower().startswith('host:'):
                return header.split(':')[1].strip()
        return None

    # Extrahiere Host aus der URL
    def extract_host_from_url(self, url):
        if url.startswith('http://'):
            url = url[7:]
        elif url.startswith('https://'):
            url = url[8:]
        return url.split('/')[0]

# Klasse zur Handhabung von Verbindungen
class Connections:
    def __init__(self, conf) -> None:
        self.config_settings = conf
        self.users = []

    def start_server(self):
        try:
            self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server.bind((self.config_settings.host, self.config_settings.port))
            self.server.listen(5)
            logging.debug(f"Proxy-Server läuft auf {self.config_settings.host}:{self.config_settings.port}")
        except Exception as e:
            logging.error(f"Fehler beim Starten des Servers: {e}")

# Hauptklasse für den Server
class Server:
    def __init__(self) -> None:
        self.config_setting = Config()
        self.filter = Filters(self.config_setting)
        self.connections = Connections(self.config_setting)
        self.connections.start_server()

    def run(self):
        while Vars.running:
            try:
                client_sock, addr = self.connections.server.accept()
                addr = addr[0]
                print(self.connections.users)
                if len(self.connections.users) == self.config_setting.max_users: 
                    client_sock.close()  # Schließe die Verbindung, wenn max. Nutzerzahl erreicht
                    continue
                if addr not in self.connections.users:
                    self.connections.users.append(addr) 
                threading.Thread(target=self.filter.through_filters, daemon=True, args=(client_sock, addr)).start()
            except KeyboardInterrupt:
                Vars.running = False
                raise KeyboardInterrupt
            except Exception as e:
                logging.error(f"Fehler: {e}")
                if addr in self.connections.users:
                    self.connections.users.remove(addr)

    def tabulate(self):
        for ser in self.connections.users:
            ser.close()
        print("Server wird beendet...")

# Einstiegspunkt
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    colorama.init(autoreset=True)

    server = Server()
    atexit.register(server.tabulate)
    try:
        server.run()
    except KeyboardInterrupt:
        print("Server wird beendet...")
    finally:
        pass  # Hier kann optionaler Cleanup-Code eingefügt werden
