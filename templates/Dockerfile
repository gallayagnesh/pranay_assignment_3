# Use the official Python image as the base image
FROM python:3.10-slim

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install the Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY . .

# Expose port 8080 for the Flask application
EXPOSE 8080

# Set the environment variable for Flask
ENV FLASK_APP=main.py
ENV FLASK_ENV=production

# Run the Flask application using Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "main:app"]
