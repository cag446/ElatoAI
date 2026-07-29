"""
Stub de VoiceHistory para el repo público.

La implementación completa (usada en producción por DebBot) añade:
  - Persistencia de historial de conversación por dispositivo MAC.
  - Contexto cruzado con Telegram: get_telegram_context() inyecta los últimos
    N mensajes del canal de Telegram para que el asistente de voz tenga
    conciencia de lo que se habló en ese canal.
  - Registro de sesiones con timestamps.

Con este stub el bridge funciona igual que sin VoiceHistory:
  - start_session() / close_session() no hacen nada.
  - get_telegram_context() devuelve lista vacía (sin contexto cruzado).
  - save_message() descarta el mensaje (sin persistencia).

Para activar la funcionalidad completa, reemplaza este archivo con la
implementación real de VoiceHistory en la Mac mini.
"""


class VoiceHistory:
    def __init__(self, device_mac: str = "?"):
        self.device_mac = device_mac

    def start_session(self):
        pass

    def close_session(self):
        pass

    def get_telegram_context(self, n: int) -> list[dict]:
        """Returns the last n Telegram messages as chat history entries."""
        return []

    def save_message(self, role: str, content: str):
        pass
