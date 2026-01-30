import socket
import time
import threading
import logging
from datetime import datetime
import psutil
import netifaces
import requests
import speedtest
import subprocess
import json
from typing import Dict, Any, Optional, Tuple

class InternetMonitor:
    def __init__(self):
        self.logger = self._setup_logger()
        self.last_down_time = None
        self.traffic_stats = {'sent': 0, 'recv': 0}
        self._init_traffic_baseline()
        self.external_ip = "N/A"
        self.provider = "N/A"
        self._update_external_info()

    def _setup_logger(self):
        logger = logging.getLogger('InternetMonitor')
        logger.setLevel(logging.INFO)
        handler = logging.FileHandler('internet_events.log', encoding='utf-8')
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return logger

    def _init_traffic_baseline(self):
        """Инициализация базовых значений трафика"""
        net_io = psutil.net_io_counters()
        self.traffic_stats['sent'] = net_io.bytes_sent
        self.traffic_stats['recv'] = net_io.bytes_recv

    def check_internet_availability(self, timeout=5) -> Dict[str, Any]:
        """
        Проверка доступности интернета с обработкой ошибок HTTP
        Возвращает словарь с результатами проверки
        """
        test_urls = [
            "https://www.google.com",
            "https://www.cloudflare.com",
            "https://1.1.1.1"
        ]
        
        for url in test_urls:
            try:
                start = time.time()
                response = requests.get(url, timeout=timeout, 
                                     headers={'User-Agent': 'InternetMonitor/1.0'})
                ping_ms = (time.time() - start) * 1000
                
                # Логируем ошибки 4xx/5xx
                if response.status_code >= 400:
                    self.logger.warning(f"HTTP Error {response.status_code} for {url}")
                    
                return {
                    'available': 200 <= response.status_code < 400,
                    'status_code': response.status_code,
                    'ping': round(ping_ms, 2),
                    'test_url': url
                }
                
            except requests.exceptions.ConnectionError:
                continue
            except requests.exceptions.Timeout:
                continue
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Request exception: {e}")
                continue
        
        # Если все проверки провалились
        if self.last_down_time is None:
            self.last_down_time = datetime.now()
            self.logger.critical("INTERNET DOWN - Initial detection")
        return {'available': False, 'status_code': 0, 'ping': None, 'test_url': None}

    def measure_speed(self) -> Dict[str, Optional[float]]:
        """
        Измерение скорости интернета в отдельном потоке
        """
        result = {'download': None, 'upload': None}
        try:
            st = speedtest.Speedtest(timeout=5)
            st.get_best_server()
            result['download'] = st.download() / 1_000_000  # Мбит/с
            result['upload'] = st.upload() / 1_000_000      # Мбит/с
        except Exception as e:
            self.logger.error(f"Speedtest failed: {e}")
        return result

    def get_network_info(self) -> Dict[str, Any]:
        """
        Сбор всей сетевой информации
        """
        info = {
            'hostname': socket.gethostname(),
            'local_ip': 'N/A',
            'mac_address': 'N/A',
            'interface_name': 'N/A',
            'external_ip': self.external_ip,
            'provider': self.provider
        }
        
        # Получаем активные интерфейсы
        try:
            for interface in netifaces.interfaces():
                addrs = netifaces.ifaddresses(interface)
                if netifaces.AF_INET in addrs and interface != 'lo':
                    info['interface_name'] = interface
                    info['local_ip'] = addrs[netifaces.AF_INET][0]['addr']
                    
                    if netifaces.AF_LINK in addrs:
                        info['mac_address'] = addrs[netifaces.AF_LINK][0]['addr']
                    break
        except Exception as e:
            self.logger.error(f"Network info error: {e}")
            
        return info

    def get_traffic_usage(self) -> Dict[str, Any]:
        """
        Сбор статистики по использованию трафика
        """
        net_io = psutil.net_io_counters()
        current_sent = net_io.bytes_sent
        current_recv = net_io.bytes_recv
        
        sent_diff = current_sent - self.traffic_stats['sent']
        recv_diff = current_recv - self.traffic_stats['recv']
        
        # Обновляем базовые значения
        self.traffic_stats['sent'] = current_sent
        self.traffic_stats['recv'] = current_recv
        
        return {
            'sent_total_mb': round(current_sent / 1_048_576, 2),
            'recv_total_mb': round(current_recv / 1_048_576, 2),
            'sent_rate_kbps': round(sent_diff * 8 / 1024 / 10, 2),  # за 10 секунд
            'recv_rate_kbps': round(recv_diff * 8 / 1024 / 10, 2)
        }

    def get_network_processes(self) -> list:
        """
        Получение списка процессов, использующих сеть
        """
        processes = []
        try:
            for conn in psutil.net_connections(kind='inet'):
                if conn.status == psutil.CONN_ESTABLISHED and conn.pid:
                    try:
                        p = psutil.Process(conn.pid)
                        processes.append({
                            'pid': conn.pid,
                            'name': p.name(),
                            'local_address': f"{conn.laddr.ip}:{conn.laddr.port}",
                            'remote_address': f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else 'N/A',
                            'status': conn.status
                        })
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
        except Exception as e:
            self.logger.error(f"Process scan error: {e}")
            
        return processes[:20]  # Ограничиваем вывод

    def _update_external_info(self):
        """Обновление внешнего IP и информации о провайдере"""
        try:
            # Получаем внешний IP
            ip_response = requests.get('https://api.ipify.org?format=json', timeout=5)
            self.external_ip = ip_response.json().get('ip', 'N/A')
            
            # Получаем информацию о провайдере (используем ip-api.com)
            if self.external_ip != 'N/A':
                provider_response = requests.get(f'http://ip-api.com/json/{self.external_ip}?fields=isp,org', timeout=5)
                if provider_response.status_code == 200:
                    data = provider_response.json()
                    self.provider = data.get('isp', data.get('org', 'N/A'))
        except Exception as e:
            self.logger.error(f"External info update failed: {e}")

    def get_all_stats(self) -> Dict[str, Any]:
        """
        Основной метод для сбора всех статистик
        """
        availability = self.check_internet_availability()
        
        # Запускаем speedtest в отдельном потоке, если интернет доступен
        speed_result = {'download': None, 'upload': None}
        if availability['available']:
            speed_thread = threading.Thread(
                target=lambda: speed_result.update(self.measure_speed())
            )
            speed_thread.daemon = True
            speed_thread.start()
            speed_thread.join(timeout=8)  # Ждем максимум 8 секунд
        
        # Формируем полный ответ
        stats = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'availability': availability,
            'speed': speed_result,
            'network_info': self.get_network_info(),
            'traffic': self.get_traffic_usage(),
            'processes': self.get_network_processes(),
            'last_down': self.last_down_time.strftime('%Y-%m-%d %H:%M:%S') if self.last_down_time else 'Never'
        }
        
        # Сбрасываем время последнего падения, если интернет восстановился
        if availability['available'] and self.last_down_time:
            downtime = datetime.now() - self.last_down_time
            self.logger.info(f"INTERNET RESTORED after {downtime}")
            self.last_down_time = None
            
        return stats