import os
import requests
import zipfile
import subprocess
import time
from flask import Flask, request, jsonify
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup

app = Flask(__name__)

# URLs for the Chrome Headless Shell and ChromeDriver
chrome_headless_url = "https://storage.googleapis.com/chrome-for-testing-public/126.0.6478.182/linux64/chrome-headless-shell-linux64.zip"
chrome_driver_url = "https://storage.googleapis.com/chrome-for-testing-public/126.0.6478.182/linux64/chromedriver-linux64.zip"

# Paths to save the downloaded files
chrome_headless_zip_path = "chrome-headless-shell-linux64.zip"
chrome_driver_zip_path = "chromedriver-linux64.zip"

# Paths for extracted files
chrome_headless_path = "chrome-headless-shell-linux64/chrome-headless-shell-linux64/chrome-headless-shell"
chrome_driver_path = "chromedriver-linux64/chromedriver-linux64/chromedriver"

def download_file(url, local_path):
    response = requests.get(url, stream=True)
    response.raise_for_status()
    with open(local_path, 'wb') as file:
        for chunk in response.iter_content(chunk_size=8192):
            file.write(chunk)
    print(f"Downloaded {url} to {local_path}")

def unzip_file(zip_path, extract_to):
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_to)
    print(f"Extracted {zip_path} to {extract_to}")

# Check if Chrome Headless Shell and ChromeDriver are already available
if not os.path.isfile(chrome_headless_path) or not os.path.isfile(chrome_driver_path):
    # Download Chrome Headless Shell and ChromeDriver if not already present
    if not os.path.isfile(chrome_headless_zip_path):
        download_file(chrome_headless_url, chrome_headless_zip_path)
    if not os.path.isfile(chrome_driver_zip_path):
        download_file(chrome_driver_url, chrome_driver_zip_path)

    # Unzip the downloaded files
    if not os.path.isfile(chrome_headless_path):
        unzip_file(chrome_headless_zip_path, "chrome-headless-shell-linux64")
    if not os.path.isfile(chrome_driver_path):
        unzip_file(chrome_driver_zip_path, "chromedriver-linux64")

    # Set executable permissions
    subprocess.run(["chmod", "+x", chrome_headless_path])
    subprocess.run(["chmod", "+x", chrome_driver_path])
else:
    print("Chrome Headless Shell and ChromeDriver are already available.")

def get_page_source(url):
    # Set up Chrome options
    chrome_options = Options()
    chrome_options.binary_location = chrome_headless_path
    chrome_options.add_argument("--headless")  # Run Chrome in headless mode
    chrome_options.add_argument("--no-sandbox")  # Required for some environments
    chrome_options.add_argument("--disable-dev-shm-usage")  # Required for some environments

    # Set up ChromeDriver service
    service = Service(executable_path=chrome_driver_path)

    # Initialize Chrome webdriver with options
    driver = webdriver.Chrome(service=service, options=chrome_options)

    try:
        # Open the URL
        driver.get(url)

        # Wait for 2 seconds
        time.sleep(2)

        # Get the HTML source of the page
        page_source = driver.page_source

        # Prettify the HTML source using BeautifulSoup
        soup = BeautifulSoup(page_source, 'html.parser')
        pretty_html = soup.prettify()

        return pretty_html
    finally:
        # Quit the driver
        driver.quit()

@app.route('/scrape', methods=['POST'])
def scrape():
    data = request.json
    url = data.get('url')
    if not url:
        return jsonify({"error": "URL is required"}), 400

    pretty_html = get_page_source(url)
    return jsonify({"page_source": pretty_html})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3090)
