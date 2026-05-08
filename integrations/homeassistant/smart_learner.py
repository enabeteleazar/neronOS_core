class SmartMatcher:
    def __init__(self, registry, room_learner, synonym_learner):
        self.registry = registry
        self.rooms = room_learner
        self.synonyms = synonym_learner

    def resolve(self, text, room=None, device=None):

        text_l = text.lower()

        # 1. SYNONYME LEARNED
        for w in text_l.split():
            resolved = self.synonyms.resolve(w)
            if resolved:
                return resolved

        # 2. ROOM LEARNED
        if room:
            learned = self.rooms.get(room)
            if learned:
                return learned[0]

        # 3. DEVICE MATCH
        candidates = []
        if device:
            for e in self.registry.entities:
                if device in e["entity_id"]:
                    candidates.append(e["entity_id"])

        if candidates:
            return self._best_match(candidates, text_l)

        # 4. FUZZY ROOM SEARCH
        for r, ents in self.rooms.map.items():
            if r in text_l:
                return ents[0]

        # 5. GLOBAL FALLBACK
        return self._fallback(text_l)

    def _best_match(self, candidates, text):
        for c in candidates:
            if any(word in c for word in text.split()):
                return c
        return candidates[0]

    def _fallback(self, text):
        for e in self.registry.entities:
            eid = e["entity_id"]
            if any(word in eid for word in text.split()):
                return eid
        return None
