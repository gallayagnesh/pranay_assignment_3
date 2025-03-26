from flask import Flask, render_template
import os

app = Flask(__name__)

@app.route('/')
def home():
    # Background color set by Cloud Run (not app logic!)
    bg_color = os.getenv("BACKGROUND_COLOR", "white")
    return render_template('index.html', bg_color=bg_color)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))