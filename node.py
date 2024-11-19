import signal
import socket
import threading
import json
import sys
from datetime import datetime
import random
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)
app.secret_key = "secret-key-for-flask"

# Store all nodes
nodes = {}
global_transaction_index = {}  # Shared transaction index across the network
global_balances = {}  # Global balances for multiple accounts
UDP_PORT = 50000  # UDP port for communication

# Helper: Generate a unique ID
def generate_unique_id(nickname):
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{nickname}-{timestamp}"

# Transaction class
class Transaction:
    def __init__(self, txn_id=None, account_id=None, amount=None, sender=None, receiver=None, **kwargs):
        self.id = txn_id or kwargs.get("id")
        self.account_id = account_id
        self.amount = amount
        self.sender = sender
        self.receiver = receiver
        self.timestamp = datetime.now().isoformat()  # Include timestamp for accurate ordering

    def to_dict(self):
        return {
            "id": self.id,
            "account_id": self.account_id,
            "amount": self.amount,
            "sender": self.sender,
            "receiver": self.receiver,
            "timestamp": self.timestamp,
        }

# Node class
class Node:
    def __init__(self, nickname):
        self.nickname = nickname
        self.node_id = generate_unique_id(nickname)  # Unique node ID
        self.address = self.assign_random_port()
        self.accounts = {f"Balance: ": 1000.0}  # Multiple accounts supported
        self.peers = []
        self.transactions = {}
        self.running = True
        self.transaction_counter = 0

    def assign_random_port(self):
        host = "127.0.0.1"
        port = random.randint(10000, 20000)
        return f"{host}:{port}"

    def process_transaction(self, txn):
        if txn.id in self.transactions:
            return False, "Transaction already processed."

        # Handle sender account
        if txn.sender in self.accounts:
            if self.accounts[txn.sender] < txn.amount:
                return False, "Insufficient balance."
            self.accounts[txn.sender] -= txn.amount

        # Handle receiver account
        if txn.receiver not in self.accounts:
            self.accounts[txn.receiver] = 0.0
        self.accounts[txn.receiver] += txn.amount

        self.transactions[txn.id] = txn
        global_transaction_index[txn.id] = txn.to_dict()
        return True, "Transaction processed successfully."

    def broadcast_transaction(self, txn):
        message = json.dumps({"type": "transaction", "data": txn.to_dict()})
        self.send_udp_message(message)

    def send_udp_message(self, message):
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for peer in self.peers:
            peer_host, peer_port = peer.split(":")
            udp_socket.sendto(message.encode("utf-8"), (peer_host, int(peer_port)))

    def listen(self):
        host, port = self.address.split(":")
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.bind((host, int(port)))
        print(f"{self.nickname} listening on {self.address} (UDP)")
        while self.running:
            data, addr = udp_socket.recvfrom(1024)
            threading.Thread(target=self.handle_udp_message, args=(data, addr)).start()

    def handle_udp_message(self, data, addr):
        try:
            message = json.loads(data.decode("utf-8"))
            if message["type"] == "transaction":
                txn_data = message["data"]
                txn = Transaction(**txn_data)
                self.process_transaction(txn)
        except Exception as e:
            print(f"Error handling UDP message: {e}")

    def stop(self):
        self.running = False

@app.route("/")
def serve_frontend():
    return send_from_directory(".", "frontend.html")

@app.route("/nodes", methods=["GET"])
def get_nodes():
    return jsonify({name: {"address": node.address, "balances": node.accounts, "node_id": node.node_id} for name, node in nodes.items()})

@app.route("/create_node", methods=["POST"])
def create_node():
    nickname = request.json.get("nickname")

    if nickname in nodes:
        return jsonify({"error": f"Node {nickname} already exists."}), 400

    new_node = Node(nickname)
    nodes[nickname] = new_node

    new_node.peers = [node.address for node in nodes.values() if node.nickname != nickname]
    for existing_node in nodes.values():
        if existing_node.nickname != nickname:
            existing_node.peers.append(new_node.address)

    threading.Thread(target=new_node.listen, daemon=True).start()

    # Sync data to the new node
    sync_data_to_new_node(new_node)

    return jsonify({"message": f"Node {nickname} created successfully.", "address": new_node.address}), 201

@app.route("/remove_node/<nickname>", methods=["DELETE"])
def remove_node(nickname):
    node = nodes.pop(nickname, None)
    if not node:
        return jsonify({"error": f"Node {nickname} not found."}), 404

    # Remove the node from the peers list of all other nodes
    for other_node in nodes.values():
        if node.address in other_node.peers:
            other_node.peers.remove(node.address)

    node.stop()  # Stop the node's listener
    return jsonify({"message": f"Node {nickname} removed successfully."}), 200


@app.route("/reset_nodes", methods=["POST"])
def reset_nodes():
    global nodes
    nodes.clear()
    return jsonify({"message": "All nodes have been reset and balances are restored to 1000"}), 200


@app.route("/create_transaction/<node_nickname>", methods=["POST"])
def create_transaction(node_nickname):
    node = nodes.get(node_nickname)
    if not node:
        return jsonify({"error": f"Node {node_nickname} not found."}), 404

    txn_id = f"txn-{node.transaction_counter}"
    node.transaction_counter += 1

    sender = request.json.get("sender")
    receiver = request.json.get("receiver")
    amount = float(request.json.get("amount"))

    txn = Transaction(txn_id, sender, amount, sender=sender, receiver=receiver)
    success, message = node.process_transaction(txn)
    if not success:
        return jsonify({"error": message}), 400

    node.broadcast_transaction(txn)
    return jsonify({"message": message, "transaction_id": txn_id}), 201

@app.route("/sync_data", methods=["GET"])
def sync_data():
    return jsonify({"transactions": global_transaction_index, "balances": global_balances})

def sync_data_to_new_node(new_node):
    """Send all existing transactions and balances to the new node."""
    for txn_id, txn_data in global_transaction_index.items():
        txn = Transaction(**txn_data)
        new_node.process_transaction(txn)

def signal_handler(sig, frame):
    for node in nodes.values():
        node.stop()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
