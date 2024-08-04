# Use an official Python runtime as a parent image
FROM python:3.8-slim

# Set the working directory in the container
WORKDIR /app

# Update package lists and debug
RUN apt-get update && \
    apt-get install -y --no-install-recommends libnss3 libnss3-dev libnspr4 libnspr4-dev && \
    apt-get -f install && \
    apt-get clean

# Copy the current directory contents into the container at /app
COPY . /app

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Make port  available to the world outside this container
EXPOSE 3080

# Define environment variable
ENV NAME World

# Run main.py when the container launches
CMD ["python", "main.py"]
