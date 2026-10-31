# Use an official Python runtime as a parent image
FROM python:3.13-slim

# Pull in Debian security fixes newer than the base image
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /usr/src/app

# Copy the current directory contents into the container at /usr/src/app
COPY . .

# Install any needed packages, then remove the installer tooling: the app doesn't need it at
# runtime, and pip bundles its own urllib3/msgpack copies that lag behind security fixes
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir --upgrade -r requirements.txt \
    && pip uninstall -y setuptools wheel pip

# Run as an unprivileged user
RUN useradd --create-home --uid 10001 app
USER app

# Make port 8000 available to the world outside this container
EXPOSE 8000

# Define environment variable
ENV MODULE_NAME="main"

# Run app.py when the container launches
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
