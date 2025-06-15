import csv
import random

# Parameters
num_brokers = 20
num_producers = 100
num_consumers = 100
num_topics = 2000
topics_per_producer = 10
topics_per_consumer = 10
replication_factor = 3

# Generate Brokers
brokers = [f'B{i}' for i in range(1, num_brokers + 1)]

# Generate Producers
producers = [f'P{i}' for i in range(1, num_producers + 1)]

# Generate Consumers
consumers = [f'C{i}' for i in range(1, num_consumers + 1)]

# Generate Topics
topics = [f'T{i}' for i in range(1, num_topics + 1)]

# Create Nodes CSV
with open('output/nodes.csv', 'w', newline='') as csvfile:
    fieldnames = ['id:ID', 'name', 'type', ':LABEL']
    writer = csv.DictWriter(csvfile, fieldnames=fieldnames, delimiter=";")
    writer.writeheader()

    # Write Brokers
    for broker in brokers:
        writer.writerow({'id:ID': broker, 'name': broker, 'type': 'broker', ':LABEL': 'Broker'})

    # Write Producers
    for producer in producers:
        writer.writerow({'id:ID': producer, 'name': producer, 'type': 'producer', ':LABEL': 'Producer'})

    # Write Consumers
    for consumer in consumers:
        writer.writerow({'id:ID': consumer, 'name': consumer, 'type': 'consumer', ':LABEL': 'Consumer'})

    # Write Topics
    for topic in topics:
        writer.writerow({'id:ID': topic, 'name': topic, 'type': 'topic', ':LABEL': 'Topic'})

# Create Relationships CSV
with open('output/relationships.csv', 'w', newline='') as csvfile:
    fieldnames = [':START_ID', ':END_ID', 'relationship', ':TYPE']
    writer = csv.DictWriter(csvfile, fieldnames=fieldnames, delimiter=";")
    writer.writeheader()

    # Producer to Topic (PUBLISHES)
    for producer in producers:
        published_topics = random.sample(topics, topics_per_producer)
        for topic in published_topics:
            writer.writerow({
                ':START_ID': producer,
                ':END_ID': topic,
                'relationship': 'publishes',
                ':TYPE': 'PUBLISHES'
            })

    # Consumer to Topic (SUBSCRIBES_TO)
    for consumer in consumers:
        subscribed_topics = random.sample(topics, topics_per_consumer)
        for topic in subscribed_topics:
            writer.writerow({
                ':START_ID': consumer,
                ':END_ID': topic,
                'relationship': 'subscribes_to',
                ':TYPE': 'SUBSCRIBES_TO'
            })

    # Broker to Topic (HOSTS)
    for topic in topics:
        hosting_brokers = random.sample(brokers, replication_factor)
        for broker in hosting_brokers:
            writer.writerow({
                ':START_ID': broker,
                ':END_ID': topic,
                'relationship': 'hosts',
                ':TYPE': 'HOSTS'
            })
