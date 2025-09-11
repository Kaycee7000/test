from flask import Flask, jsonify
import os

app = Flask(__name__)

@app.route('/')
def index():
    return jsonify({
        'message': 'Hello from Echo App',
        'version': os.getenv('APP_VERSION', 'v0.1'),
        'pod': os.getenv('POD_NAME', 'local')
    })

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
