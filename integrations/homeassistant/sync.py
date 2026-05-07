def sync(client, registry, learner):
    """
    Synchronise les entités Home Assistant
    et entraîne le room learner
    """

    states = client.get_states()

    registry.load(states)

    learner.learn(states)

    return states
