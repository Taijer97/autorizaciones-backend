from fastapi import WebSocket
import json
import logging
import asyncio
from typing import List, Optional
import redis.asyncio as aioredis
from app.config import settings

logger = logging.getLogger("websocket")
logging.basicConfig(level=logging.INFO)

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.redis_client: Optional[aioredis.Redis] = None
        self.pubsub_task: Optional[asyncio.Task] = None

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"Nuevo cliente WebSocket conectado. Conexiones activas: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"Cliente WebSocket desconectado. Conexiones activas: {len(self.active_connections)}")

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str):
        """Envía el mensaje a todos los clientes conectados a este proceso."""
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception as e:
                logger.warning(f"Error al enviar mensaje a WebSocket, desconectando: {e}")
                disconnected.append(connection)
        for connection in disconnected:
            self.disconnect(connection)

    async def publish_event(self, event_type: str, data: dict):
        """Publica un evento. Si Redis está disponible, lo publica en Redis PubSub. Si no, hace broadcast local."""
        event = {
            "type": event_type,
            "data": data
        }
        event_str = json.dumps(event)
        
        if self.redis_client:
            try:
                await self.redis_client.publish("authorization_events", event_str)
                logger.info(f"Evento {event_type} publicado en Redis PubSub")
                return
            except Exception as e:
                logger.error(f"Error al publicar en Redis PubSub (usando fallback local): {e}")
        
        # Fallback local
        await self.broadcast(event_str)

    async def start_redis_listener(self):
        """Escucha eventos de Redis PubSub y los retransmite localmente."""
        try:
            self.redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            # Test connection
            await self.redis_client.ping()
            logger.info("Conectado exitosamente a Redis para WebSockets.")
            
            pubsub = self.redis_client.pubsub()
            await pubsub.subscribe("authorization_events")
            
            async def listen():
                try:
                    async for message in pubsub.listen():
                        if message["type"] == "message":
                            # Broadcast the payload to all local connections
                            await self.broadcast(message["data"])
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"Error en listener de PubSub: {e}")
                    await asyncio.sleep(5)
                    asyncio.create_task(self.start_redis_listener())

            self.pubsub_task = asyncio.create_task(listen())
        except Exception as e:
            logger.warning(f"No se pudo conectar a Redis ({e}). Usando modo in-memory local para WebSockets.")
            self.redis_client = None

    async def close(self):
        if self.pubsub_task:
            self.pubsub_task.cancel()
        if self.redis_client:
            await self.redis_client.close()

manager = ConnectionManager()
