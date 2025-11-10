FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /mindbloat

# Install system dependencies required for building Python packages
# build-essential contains compilers like gcc needed for some pip packages
RUN apt-get update && apt-get install -y --no-install-recommends build-essential

# The docker-compose build context already clones the repo.
# We just need to copy the contents into our WORKDIR.
COPY . .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Make port 8080 available to the world outside this container
EXPOSE 8080

# Run the three python scripts in parallel
# The container will exit if any of the scripts stop
CMD ["sh", "-c", "python bot.py & python subs.py & python cron.py & wait -n"]