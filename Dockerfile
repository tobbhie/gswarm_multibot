FROM python:3.11-slim

# Install Go and build gswarm
COPY gswarm_builder.sh /tmp/gswarm_builder.sh
RUN chmod +x /tmp/gswarm_builder.sh && /tmp/gswarm_builder.sh

# Copy gswarm binary to PATH
RUN cp /root/go/bin/gswarm /usr/local/bin/gswarm && chmod +x /usr/local/bin/gswarm

# Copy Python app
WORKDIR /app
COPY . .

# Create configs directory
RUN mkdir -p /app/configs

# Install Python dependencies
RUN pip install -r requirements.txt

ENV TELEGRAM_BOT_TOKEN=
CMD ["python", "main.py"]
