# Use an official Python runtime as a parent image
FROM python:3.8-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y wget unzip \
    # Install Chrome dependencies
    fonts-liberation \
    libappindicator3-1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libgdk-pixbuf2.0-0 \
    libnspr4 \
    libnss3 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    xdg-utils \
    libgbm-dev

# Download and extract Chrome headless shell
RUN wget -O chrome-headless-shell-linux64.zip https://storage.googleapis.com/chrome-for-testing-public/126.0.6478.182/linux64/chrome-headless-shell-linux64.zip && \
    unzip chrome-headless-shell-linux64.zip -d /app && \
    chmod +x /app/chrome-headless-shell-linux64/chrome-headless-shell

# Download and extract ChromeDriver
RUN wget -O chromedriver-linux64.zip https://storage.googleapis.com/chrome-for-testing-public/126.0.6478.182/linux64/chromedriver-linux64.zip && \
    unzip chromedriver-linux64.zip -d /app && \
    chmod +x /app/chromedriver-linux64/chromedriver-linux64/chromedriver

# Copy the current directory contents into the container at /app
COPY . /app

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Make port available to the world outside this container
EXPOSE 3090

# Run main.py when the container launches
CMD ["python", "main.py"]
