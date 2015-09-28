import logging


class Broker(object):
    """Broker class object, consisting of following attributes
        -id: Id of broker
        -partitions: partitions under a given broker
    """
    def __init__(self, id, partitions=None):
        self._id = id
        self._partitions = partitions or set()
        self.log = logging.getLogger(self.__class__.__name__)

    def get_hostname(self, zk):
        """Get hostname of broker from zookeeper."""
        try:
            hostname = zk.get_brokers(self._id)
            result = hostname[self._id]['host']
        except KeyError:
            self.log.warning(
                'Unknown host for broker {broker}. Returning as'
                ' localhost'.format(broker=self._id)
            )
            result = 'localhost'
        return result

    @property
    def partitions(self):
        return self._partitions

    @property
    def id(self):
        return self._id

    @property
    def topics(self):
        """Return the set of topics current in broker."""
        return set([partition.topic for partition in self._partitions])

    def remove_partition(self, partition):
        """Remove partition from partition list."""
        if partition in self._partitions:
            # Remove partition from set
            self._partitions.remove(partition)
            # Remove broker from replica list of partition
            partition.replicas.remove(self)
        else:
            raise ValueError(
                'Partition: {topic_id}:{partition_id} not found in broker '
                '{broker_id}'.format(
                    topic_id=partition.topic.id,
                    partition_id=partition.partition_id,
                    broker_id=self._id,
                )
            )

    def add_partition(self, partition):
        """Add partition to partition list."""
        assert(partition not in self._partitions)
        # Add partition to existing set
        self._partitions.add(partition)
        # Add broker to replica list
        partition.replicas.append(self)

    def move_partition(self, partition, broker_destination):
        """Move partition to destination broker and adjust replicas."""
        self.remove_partition(partition)
        broker_destination.add_partition(partition)

    def count_partitions(self, topic):
        """Return count of partitions for given topic."""
        return sum([
            1
            for p in self._partitions
            if p.topic == topic
        ])

    def count_preferred_replica(self):
        """Return number of times broker is set as preferred leader."""
        return sum(
            [1 for partition in self.partitions if partition.leader == self],
        )

    def decrease_leader_count(self, partitions, leaders_per_broker, opt_count):
        """Re-order eligible replicas to balance preferred leader assignment.

        @params:
        self:               Current object is leader-broker with > opt_count as
                            leaders and will be tried to reduce the same.
        partitions:         Set of all partitions in the cluster.
        leaders_per_broker: Broker-as-leader-count per broker.
        opt_count:          Optimal value for each broker to act as leader.
        """
        # Generate a list of partitions for which we can change the leader.
        # Filter out partitions with one replica (Replicas cannot be changed).
        # self is current-leader
        possible_partitions = [
            partition
            for partition in partitions
            if self == partition.leader and len(partition.replicas) > 1
        ]
        for possible_victim_partition in possible_partitions:
            for possible_new_leader in possible_victim_partition.followers:
                if (leaders_per_broker[possible_new_leader] <= opt_count and
                        leaders_per_broker[self] -
                        leaders_per_broker[possible_new_leader] > 1):
                    victim_partition = possible_victim_partition
                    new_leader = possible_new_leader
                    victim_partition.swap_leader(new_leader)
                    leaders_per_broker[new_leader] += 1
                    leaders_per_broker[self] -= 1
                    break
            if leaders_per_broker[self] == opt_count:
                return

    def is_relatively_unbalanced(self, broker_dest, extra_partition_per_broker):
        """Return true if brokers are relatively unbalanced based on partition
        count.

        Brokers are relatively unbalanced in terms of partition count if the
        difference b/w their partition-count is > allowed-max difference
        governed by 'extra_partition_per_broker' variable.
        """
        return (len(self.partitions) - len(broker_dest.partitions) >
                extra_partition_per_broker)

    def get_eligible_partition(self, broker_destination):
        """Return best eligible partition in broker to be transferred to
        destination-broker.

        Conditions:
        @ partition in source should not be present in destination broker
        """
        # Based on partition present in broker but not in broker_destination
        # Only partitions not having replica in broker_destination are valid
        valid_source_partitions = [
            partition
            for partition in self.partitions
            if partition not in [p for p in broker_destination.partitions]
        ]
        valid_dest_partitions = [
            partition
            for partition in broker_destination.partitions
            if partition not in [p for p in self.partitions]
        ]
        # Get best fit partition, based on avoiding partition from same topic
        # and partition with least siblings in destination-broker.
        return self._get_preffered_partition(
            valid_source_partitions,
            valid_dest_partitions,
        )

    def _get_preffered_partition(self, source_partitions, dest_partitions):
        """Get partition from given source-partitions with least siblings in
        given destination partitions and sibling count.

        @key_term:
        siblings: Partitions belonging to same topic

        @params:
        source_partitions: Partitions whose siblings are counted.
        dest_partitions:   Partitions where siblings for given source
                                partitions are monitored.
        """
        preffered_partition = min(
            source_partitions,
            key=lambda source_partition:
                source_partition.count_siblings(dest_partitions),
        )
        sibling_cnt = preffered_partition.count_siblings(dest_partitions)
        return preffered_partition, sibling_cnt
