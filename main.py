import os
import json
import logging
import datetime
from flask import Flask, request, redirect, render_template, url_for
from google.cloud import storage, secretmanager
import google.generativeai as genai

# Flask App Initialization
app = Flask(__name__)

# GCP Configurations
bucket_name = os.getenv("GCS_BUCKET_NAME")
PROJECT_ID = "image-upload-gcp-project"
SECRET_NAME = "GCS_SERVICE_ACCOUNT_KEY"
GEMINI_SECRET_NAME = "GEMINI_API_KEY"

# Configure Logging
logging.basicConfig(level=logging.DEBUG)

def get_gcs_credentials():
    """Fetches Service Account JSON and Gemini API key from Secret Manager."""
    client = secretmanager.SecretManagerServiceClient()

    # Fetch GCS Service Account Key
    secret_path = f"projects/{PROJECT_ID}/secrets/{SECRET_NAME}/versions/latest"
    response = client.access_secret_version(request={"name": secret_path})
    secret_json = response.payload.data.decode("UTF-8")

    # Save to temporary file
    temp_cred_path = "/tmp/gcs_service_account.json"
    with open(temp_cred_path, "w") as f:
        f.write(secret_json)

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = temp_cred_path

    # Fetch Gemini API Key
    gemini_secret_path = f"projects/{PROJECT_ID}/secrets/{GEMINI_SECRET_NAME}/versions/latest"
    try:
        gemini_response = client.access_secret_version(request={"name": gemini_secret_path})
        gemini_api_key = gemini_response.payload.data.decode("UTF-8")
        genai.configure(api_key=gemini_api_key)  # Set API Key for Gemini AI
        logging.info("Gemini AI API Key successfully configured.")
    except Exception as e:
        logging.error(f"Failed to retrieve Gemini API Key: {e}")
        raise

    return temp_cred_path

# Initialize Google Cloud Clients
def initialize_clients():
    get_gcs_credentials()
    return storage.Client()

storage_client = initialize_clients()

def upload_to_gemini(path, mime_type="image/jpeg"):
    """Uploads image to Gemini AI for processing."""
    try:
        file = genai.upload_file(path, mime_type=mime_type)
        return file
    except Exception as e:
        logging.error(f"Failed to upload image to Gemini: {e}")
        return None

def generative_ai(image_file):
    """Sends image to Gemini AI and retrieves title & description."""
    try:
        model = genai.GenerativeModel(model_name="gemini-1.5-flash")
        files = upload_to_gemini(image_file)
        
        if not files:
            return {"title": "Upload Error", "description": "Failed to upload image to Gemini AI."}

        chat_session = model.start_chat(
            history=[{"role": "user", "parts": [files, "Generate title and description for the image and return as JSON"]}]
        )

        response = chat_session.send_message("Generate title and description in JSON format")
        logging.debug(f"Gemini API Response: {response.text}")

        response_text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(response_text)

    except json.JSONDecodeError:
        logging.error("Invalid JSON response from Gemini AI")
        return {"title": "Invalid Response", "description": "Gemini AI returned an invalid response."}
    except Exception as e:
        logging.error(f"Error in generative AI: {e}")
        return {"title": "Error", "description": "An error occurred while processing the image."}

def upload_to_gcs(bucket_name, source_file, destination_blob_name):
    """Uploads a file to Google Cloud Storage."""
    try:
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(destination_blob_name)
        blob.upload_from_filename(source_file)
        logging.info(f"Uploaded {destination_blob_name} to GCS successfully.")
        return True
    except Exception as e:
        logging.error(f"Failed to upload {source_file} to GCS: {e}")
        return False

def list_uploaded_images(bucket_name):
    """Lists all images in the GCS bucket."""
    try:
        bucket = storage_client.bucket(bucket_name)
        blobs = bucket.list_blobs()
        return [blob.name for blob in blobs if blob.name.endswith(('.jpg', '.jpeg'))]
    except Exception as e:
        logging.error(f"Failed to list images in GCS: {e}")
        return []

def generate_temporary_url(bucket_name, blob_name, expiration=3600):
    """Generates a signed URL to access private GCS images."""
    try:
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        if not blob.exists(storage_client):
            logging.error(f"File {blob_name} not found in GCS.")
            return None

        url = blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(seconds=expiration),
            method="GET"
        )
        return url
    except Exception as e:
        logging.error(f"Failed to generate signed URL for {blob_name}: {e}")
        return None

@app.route('/')
def index():
    if not bucket_name:
        return "GCS_BUCKET_NAME is not set", 500

    images = list_uploaded_images(bucket_name)
    bg_color = os.getenv("BACKGROUND_COLOR", "#f0f2f5")  # Default to original color if not set
    return render_template('index.html', images=images, bg_color=bg_color)

@app.route('/upload', methods=['POST'])
def upload():
    """Handles image upload, AI processing, and JSON metadata storage."""
    if not bucket_name:
        return "GCS_BUCKET_NAME is not set", 500

    json_path, temp_path = None, None

    try:
        if 'image' not in request.files:
            return "No file uploaded", 400

        file = request.files['image']
        if file.filename == '':
            return "No file selected", 400

        # Save file temporarily
        temp_path = os.path.join('/tmp', file.filename)
        file.save(temp_path)

        # Generate AI response
        ai_response = generative_ai(temp_path)
        title = ai_response.get('title', 'No title present')
        description = ai_response.get('description', 'No description present')

        # Save metadata as JSON
        json_data = {"title": title, "description": description}
        json_filename = os.path.splitext(file.filename)[0] + '.json'
        json_path = os.path.join('/tmp', json_filename)
        with open(json_path, 'w') as json_file:
            json.dump(json_data, json_file)

        # Upload to GCS
        if not upload_to_gcs(bucket_name, temp_path, file.filename) or not upload_to_gcs(bucket_name, json_path, json_filename):
            return "File upload failed", 500

    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        return "Internal Server Error", 500

    finally:
        # Cleanup temporary files
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        if json_path and os.path.exists(json_path):
            os.remove(json_path)

    return redirect(url_for('view_image', filename=file.filename))

@app.route('/view')
def view_image():
    """Fetches and displays metadata along with signed URL for image."""
    filename = request.args.get('filename')

    if not filename:
        return "No file specified", 400

    json_filename = os.path.splitext(filename)[0] + '.json'
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(json_filename)

    if not blob.exists():
        logging.error(f"Metadata file {json_filename} not found in bucket.")
        return "Metadata not found", 404

    try:
        json_data = json.loads(blob.download_as_text())
    except json.JSONDecodeError:
        logging.error(f"Invalid JSON in metadata file: {json_filename}")
        return "Invalid metadata format", 500

    title = json_data.get('title', 'No title available')
    description = json_data.get('description', 'No description available')

    # Generate a temporary URL for secure access
    temp_url = generate_temporary_url(bucket_name, filename)
    logging.debug(f"Generated Signed URL: {temp_url}")

    if not temp_url:
        return "Error generating image URL", 500

    bg_color = os.getenv("BACKGROUND_COLOR", "#f0f2f5")  # Default to original color if not set
    return render_template('view.html', image_url=temp_url, title=title, description=description, bg_color=bg_color)
    
if __name__ == '__main__':
    app.run(port=8080, debug=True)
