FROM grafana/grafana:10.3.4

# Install MongoDB plugin
RUN grafana-cli plugins install grafana-mongodb-datasource

# Copy provisioning files
COPY provisioning /etc/grafana/provisioning
COPY dashboards /var/lib/grafana/dashboards
