# T5 STATE (round 01, part 1)

- Done: all endpoints except the message stream (see implementation.md); tests 289 passed.
- In progress: nothing.
- Next: wire `POST /conversations/{id}/messages` to T3's turn runner (SSE, 15 s ping, same `app.state.airline` locks) once
  `it03-agent` is merged and the coordinator sends the signature; add the SSE tests then.
- Open decisions: none. (Session cookie is stateless; no server-side revocation.)
- Verify: see implementation.md "Verify".
