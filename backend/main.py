from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from paho.mqtt import client as mqtt
from pythonosc import dispatcher, osc_server
import asyncio, json

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

connections = set()
state = {"x": 0, "y": 0, "z": 0}

# ---- MQTT Setup ----
mqtt_client = mqtt.Client()

def on_mqtt_message(client, userdata, msg):
    try:
        data = float(msg.payload.decode())
        if msg.topic == "device/x": state["x"] = data
        if msg.topic == "device/y": state["y"] = data
        if msg.topic == "device/z": state["z"] = data
        broadcast()
    except Exception as e:
        print("MQTT error:", e)

mqtt_client.on_message = on_mqtt_message
mqtt_client.connect("localhost", 1883)
mqtt_client.subscribe("device/#")
mqtt_client.loop_start()

# ---- OSC Setup ----
def osc_handler(addr, *args):
    state["x"], state["y"], state["z"] = args[0:3]
    broadcast()

osc_dispatcher = dispatcher.Dispatcher()
osc_dispatcher.map("/rotation", osc_handler)

def start_osc():
    server = osc_server.ThreadingOSCUDPServer(("0.0.0.0", 9000), osc_dispatcher)
    print("OSC running on port 9000")
    server.serve_forever()

# ---- WebSocket ----
async def broadcast():
    msg = json.dumps(state)
    for ws in list(connections):
        await ws.send_text(msg)

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    connections.add(ws)
    try:
        while True:
            await asyncio.sleep(0.1)
    except:
        connections.remove(ws)

if __name__ == "__main__":
    import threading
    threading.Thread(target=start_osc, daemon=True).start()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
