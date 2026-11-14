"""
The message bus: how the fleet manager and the robots send each other messages.

Two brokers with the same interface:

    InMemoryBroker()            everything in one Python process, messages are delivered
                                at once. Used by the tests: fast, no Docker, always the same.
    MqttBroker(host, port)      a real MQTT broker (Mosquitto in Docker, see docker-compose.yml).

Both have connect(client_id, will=None), which gives a Connection:

    conn.publish(topic, message, retain=False)   message is a dict, sent as JSON
    conn.subscribe(pattern, callback)            callback(topic, message); + and # wildcards work
    conn.close()                                 a clean goodbye: the broker does NOT send the will
    conn.crash()                                 (in-memory only) vanish without a goodbye: the
                                                 broker sends the will, like when a robot loses power

The "will" (MQTT last will and testament) is a message the broker keeps for a
client and publishes for it if the connection breaks without a goodbye. Robots
use it to say "CONNECTIONBROKEN", so the manager hears about a dead robot even
though the robot itself can't tell anyone any more.

MQTT basics in two lines:
    - Nobody talks to anyone directly. Everyone publishes to TOPICS on the broker,
      and the broker passes each message to everyone who subscribed to that topic.
    - A RETAINED message is kept by the broker and given to anyone who subscribes later
      (robots use this for their connection state, so a manager that starts late still knows).
"""

import json
import socket
import threading


def topic_matches(pattern, topic):
    """MQTT wildcards: + matches one level, # matches everything below."""
    p_parts, t_parts = pattern.split("/"), topic.split("/")
    for i, p in enumerate(p_parts):
        if p == "#":
            return True
        if i >= len(t_parts):
            return False
        if p != "+" and p != t_parts[i]:
            return False
    return len(p_parts) == len(t_parts)


# ---------------------------------------------------------------------------
# In-memory broker (tests)
# ---------------------------------------------------------------------------

class InMemoryBroker:
    """A tiny MQTT-like broker inside the process. Delivery is immediate and in order."""

    def __init__(self):
        self.connections = {}      # client_id -> Connection
        self.retained = {}         # topic -> message
        self.log = []              # every (sender, topic, message), handy in tests
        self._lock = threading.RLock()

    def connect(self, client_id, will=None):
        with self._lock:
            old = self.connections.get(client_id)
            if old is not None:
                old._drop(send_will=False)          # MQTT: the same client id kicks the old one out
            conn = InMemoryConnection(self, client_id, will)
            self.connections[client_id] = conn
            return conn

    def _publish(self, sender, topic, message, retain):
        with self._lock:
            payload = json.loads(json.dumps(message))      # like the network: only JSON survives
            self.log.append((sender, topic, payload))
            if retain:
                self.retained[topic] = payload
            targets = [(conn, cb) for conn in list(self.connections.values())
                       for pattern, cb in list(conn.subscriptions) if topic_matches(pattern, topic)]
            for conn, callback in targets:
                if conn.open:
                    callback(topic, json.loads(json.dumps(payload)))

    def messages(self, pattern):
        """Everything published on topics matching pattern (for tests)."""
        return [(t, m) for _, t, m in self.log if topic_matches(pattern, t)]


class InMemoryConnection:

    def __init__(self, broker, client_id, will):
        self.broker = broker
        self.client_id = client_id
        self.will = will                 # (topic, message, retain) or None
        self.subscriptions = []
        self.open = True

    def publish(self, topic, message, retain=False):
        if not self.open:
            raise ConnectionError(f"{self.client_id} is not connected")
        self.broker._publish(self.client_id, topic, message, retain)

    def subscribe(self, pattern, callback):
        self.subscriptions.append((pattern, callback))
        for topic, message in sorted(self.broker.retained.items()):
            if topic_matches(pattern, topic):
                callback(topic, json.loads(json.dumps(message)))

    def close(self):
        self._drop(send_will=False)

    def crash(self):
        self._drop(send_will=True)

    def _drop(self, send_will):
        if not self.open:
            return
        self.open = False
        with self.broker._lock:
            if self.broker.connections.get(self.client_id) is self:
                del self.broker.connections[self.client_id]
        if send_will and self.will:
            topic, message, retain = self.will
            self.broker._publish(self.client_id, topic, message, retain)


# ---------------------------------------------------------------------------
# Real MQTT broker (Mosquitto)
# ---------------------------------------------------------------------------

class MqttBroker:
    """Connections to a real MQTT broker, with paho-mqtt."""

    def __init__(self, host="127.0.0.1", port=1883, keepalive=5, timeout=5.0):
        self.host, self.port = host, port
        self.keepalive = keepalive       # seconds; the broker sends the will ~1.5x this after a robot dies
        self.timeout = timeout

    def connect(self, client_id, will=None):
        return MqttConnection(self, client_id, will)


class MqttConnection:

    def __init__(self, broker, client_id, will):
        import paho.mqtt.client as mqtt

        self.client_id = client_id
        self.timeout = broker.timeout
        self.subscriptions = []
        self._acks = {}
        self._connected = threading.Event()
        self.open = False

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        if will:
            topic, message, retain = will
            self.client.will_set(topic, json.dumps(message), qos=1, retain=retain)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_subscribe = self._on_subscribe
        try:
            self.client.connect(broker.host, broker.port, keepalive=broker.keepalive)
        except OSError as error:
            raise ConnectionError(
                f"no MQTT broker at {broker.host}:{broker.port} ({error}). "
                f"Start one with: docker compose up -d") from None
        # Send small messages at once. Without this, TCP holds a small message back until the
        # previous one is acknowledged (Nagle's algorithm), which made every tick ~90 ms slower.
        self.client.socket().setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.client.loop_start()
        if not self._connected.wait(self.timeout):
            self.client.loop_stop()
            raise ConnectionError(f"the MQTT broker at {broker.host}:{broker.port} did not answer")
        self.open = True

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if not reason_code.is_failure:
            for pattern, _ in self.subscriptions:          # after a reconnect: subscribe again
                client.subscribe(pattern, qos=1)
            self._connected.set()

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties):
        event = self._acks.get(mid)
        if event:
            event.set()

    def _on_message(self, client, userdata, msg):
        try:
            message = json.loads(msg.payload)
        except (ValueError, UnicodeDecodeError):
            return                                         # not JSON: not for us
        for pattern, callback in list(self.subscriptions):
            if topic_matches(pattern, msg.topic):
                try:
                    callback(msg.topic, message)
                except Exception as error:                 # never kill paho's network thread
                    print(f"[{self.client_id}] error handling {msg.topic}: {error!r}")

    def publish(self, topic, message, retain=False):
        # VDA 5050: QoS 0 ("at most once") for order, instantActions and state, QoS 1 ("at least
        # once") only for connection. Measured here: QoS 0 takes ~1 ms there and back, QoS 1 ~95 ms.
        qos = 1 if topic.endswith("/connection") else 0
        self.client.publish(topic, json.dumps(message), qos=qos, retain=retain)

    def subscribe(self, pattern, callback):
        self.subscriptions.append((pattern, callback))
        event = threading.Event()
        _, mid = self.client.subscribe(pattern, qos=1)
        self._acks[mid] = event
        event.wait(self.timeout)                           # so nothing published right after is missed

    def close(self):
        if self.open:
            self.open = False
            self.client.disconnect()                       # clean: the will is NOT sent
            self.client.loop_stop()

    def crash(self):
        raise NotImplementedError("to test a crash with a real broker, kill the robot process")
