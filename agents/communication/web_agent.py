# agents/web_agent.py

from __future__ import annotations

import httpx

from core.agents.base_agent import BaseAgent, AgentResult
from core.config import settings

SEARXNG_URL         = settings.SEARXNG_URL
SEARXNG_TIMEOUT     = settings.SEARXNG_TIMEOUT
SEARXNG_MAX_RESULTS = settings.SEARXNG_MAX_RESULTS


class WebAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(name="web_agent")

    async def execute(self, query: str, **kwargs) -> AgentResult:
        # FIX: self.logger.info avec %r au lieu de concaténation
        self.logger.info("Recherche web pour : %r", query)
        start = self._timer()

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=5.0, read=SEARXNG_TIMEOUT, write=5.0, pool=5.0
                )
            ) as client:
                response = await client.get(
                    f"{SEARXNG_URL}/search",
                    params={
                        "q":          query,
                        "format":     "json",
                        "language":   "fr",
                        "safesearch": "0",
                    },
                )
                response.raise_for_status()
                data = response.json()

        except httpx.TimeoutException:
            return self._failure("searxng timeout", latency_ms=self._elapsed_ms(start))
        except httpx.ConnectError:
            return self._failure(
                f"searxng inaccessible à {SEARXNG_URL}",
                latency_ms=self._elapsed_ms(start),
            )
        except httpx.HTTPStatusError as e:
            return self._failure(
                f"searxng erreur HTTP {e.response.status_code}",
                latency_ms=self._elapsed_ms(start),
            )
        except httpx.RequestError as e:
            return self._failure(
                f"erreur réseau searxng : {e}",
                latency_ms=self._elapsed_ms(start),
            )
        except Exception as e:
            return self._failure(
                f"erreur inattendue : {e}",
                latency_ms=self._elapsed_ms(start),
            )

        results = data.get("results", [])
        latency = self._elapsed_ms(start)

        if not results:
            return self._failure("Aucun résultat trouvé", latency_ms=latency)

        top     = results[:SEARXNG_MAX_RESULTS]
        content = self._format(query, top)

        return self._success(
            content=content,
            metadata={
                "query":         query,
                "total_results": len(results),
                "returned":      len(top),
                "sources":       [r.get("url", "") for r in top],
            },
            latency_ms=latency,
        )

    def _format(self, query: str, results: list) -> str:
        lines = [f"Résultats pour : {query}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"[{i}] {r.get('title', 'Sans titre')}")
            lines.append(f"    URL : {r.get('url', '')}")
            lines.append(f"    {r.get('content', '')}")
            lines.append("")
        return "\n".join(lines)
