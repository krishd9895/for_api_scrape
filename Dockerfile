# Use an official Python runtime as a parent image
FROM python:3.8-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y \
    wget \
    libnss3 \
    libnss3-dev \
    libnspr4 \
    libnspr4-dev \
    libglib2.0-0 \
    libgobject-2.0-0 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libgio2.0-0 \
    libexpat1 \
    libatspi2.0-0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libdrm2 \
    libxcb1 \
    libxkbcommon0 \
    libasound2 \
    libc6 \
    libffi7 \
    libpcre3 \
    libsystemd0 \
    libgmodule-2.0-0 \
    zlib1g \
    libmount1 \
    libselinux1 \
    libxrender1 \
    libwayland-server0 \
    libxau6 \
    libxdmcp6 \
    librt0 \
    liblzma5 \
    liblz4-1 \
    libgcrypt20 \
    libblkid1 \
    libpcre2-8-0 \
    libbsd0 \
    libgpg-error0 \
    && rm -rf /var/lib/apt/lists/*

# Download and install chromedriver
RUN wget https://chromedriver.storage.googleapis.com/114.0.5735.90/chromedriver_linux64.zip -O /chromedriver.zip \
    && unzip /chromedriver.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/chromedriver

# Copy the current directory contents into the container at /app
COPY . /app

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Make port available to the world outside this container
EXPOSE 3080

# Define environment variable
ENV NAME World

# Run main.py when the container launches
CMD ["python", "main.py"]
