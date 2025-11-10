FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /mindbloat

# Install system dependencies, including bash
RUN apt-get update && apt-get install -y --no-install-recommends nano \
    build-essential \
    bash

# The docker-compose build context already clones the repo.
# Copy the contents from the build context into our WORKDIR.
COPY . .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Make port 8080 available to the world outside this container
EXPOSE 8080

# Run the three python scripts in parallel using bash
CMD ["bash", "-c", "python bot.py & python subs.py & python cron.py & wait -n"]
