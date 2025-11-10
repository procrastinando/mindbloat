# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /mindbloat

# Install git to clone the repository
RUN apt-get update && apt-get install -y git

# Clone the repository into the working directory
RUN git clone https://github.com/procrastinando/mindbloat.git .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Make port 8080 available to the world outside this container
EXPOSE 8080

# Run the three python scripts in parallel
# The container will exit if any of the scripts stop
CMD ["sh", "-c", "python bot.py & python subs.py & python cron.py & wait -n"]