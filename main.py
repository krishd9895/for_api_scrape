import time
from flask import Flask, request, jsonify
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

app = Flask(__name__)

# Paths to the Chrome headless shell and ChromeDriver
chrome_headless_path = "/app/chrome-headless-shell-linux64/chrome-headless-shell"
chrome_driver_path = "/app/chromedriver-linux64/chromedriver"

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

        return page_source
    finally:
        # Quit the driver
        driver.quit()

@app.route('/scrape', methods=['POST'])
def scrape():
    data = request.json
    url = data.get('url')
    if not url:
        return jsonify({"error": "URL is required"}), 400

    page_source = get_page_source(url)
    return jsonify({"page_source": page_source})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3090)
