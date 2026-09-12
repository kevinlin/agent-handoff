As of September 2026, the cleanest supported way is the **GitHub Copilot SDK**. It exposes `listModels()` / `models.list`, and GitHub describes the result as the models available to the resolved authenticated user, including capabilities, billing metadata, and policy information. ([GitHub Docs][1])

For TypeScript:

```ts
import { CopilotClient } from "@github/copilot-sdk";

const client = new CopilotClient();

await client.start();

try {
  const models = await client.listModels();

  console.log(
    models.map(model => ({
      id: model.id,
      name: model.name,
      policy: model.policy,
      billing: model.billing,
      reasoningEfforts: model.supportedReasoningEfforts,
    }))
  );
} finally {
  await client.stop();
}
```

Or via the lower-level RPC:

```ts
const { models } = await client.rpc.models.list({});
```

This is preferable to maintaining your own static list because Copilot model availability depends on the user's plan, the Copilot surface, and organization/enterprise policies. ([GitHub Docs][2])

### Authentication

The SDK uses the Copilot CLI runtime, so it can use the same authenticated GitHub identity. Copilot CLI currently accepts OAuth tokens from the GitHub CLI, its own OAuth tokens, or a fine-grained PAT with the **Copilot Requests** account permission. Classic `ghp_` PATs aren't supported. ([GitHub Docs][3])

So for your agent-handoff use case, I'd use:

```text
agent-handoff
     │
     ▼
@github/copilot-sdk
     │
     ├── client.listModels()
     │
     ▼
Copilot CLI runtime
     │
     ▼
authenticated user's Copilot entitlement
     │
     ├── subscription
     ├── org policy
     └── model availability
```

That gives you the list at runtime rather than maintaining something like:

```ts
const COPILOT_MODELS = [
  "claude-sonnet-4.6",
  "gpt-5.4",
  ...
];
```

### There is also a direct HTTP endpoint

The CLI itself currently obtains its model catalogue from a Copilot API endpoint. For a normal `github.com` account, this works:

```bash
curl -s \
  https://api.githubcopilot.com/models \
  -H "Authorization: Bearer $(gh auth token)" \
  | jq -r '.data[].id'
```

There is current evidence of this endpoint being used by the Copilot CLI itself, and direct calls return the entitled model catalogue. ([GitHub][4])

For example, the response is structurally similar to:

```json
{
  "data": [
    {
      "id": "claude-sonnet-4.6",
      "...": "..."
    },
    {
      "id": "gpt-5.4",
      "...": "..."
    }
  ]
}
```

But I **wouldn't build your integration around that URL**.

`https://api.githubcopilot.com/models` is not documented as a stable public GitHub REST API contract. More importantly, Copilot uses **per-subscription API endpoints** for some Enterprise Cloud configurations. The Copilot CLI explicitly changed to subscription-specific endpoints for this reason. ([GitHub][5])

There are even recent August 2026 Copilot CLI bugs where the CLI accidentally calls:

```text
https://api.githubcopilot.com/models
```

instead of an enterprise tenant endpoint such as:

```text
https://copilot-api.<tenant>.ghe.com/models
```

and gets `401` because the tenant's Copilot token is not valid against the public endpoint. ([GitHub][6])

So for your CLI integration, I'd rank the approaches:

| Approach                           | Subscription-aware | Org-policy-aware |             Stability | Recommendation                            |
| ---------------------------------- | -----------------: | ---------------: | --------------------: | ----------------------------------------- |
| `CopilotClient.listModels()`       |                Yes |              Yes |         Supported SDK | **Use this**                              |
| `client.rpc.models.list({})`       |                Yes |              Yes |         Supported RPC | Good if you already use RPC               |
| `GET api.githubcopilot.com/models` |                Yes |          Usually | Internal-ish endpoint | Avoid as primary integration              |
| GitHub REST `/copilot/...` APIs    |                N/A |              N/A |                Public | No model-catalog API currently documented |

One particularly useful detail for your harness is that `listModels()` also exposes **billing information dynamically**. GitHub explicitly recommends reading model pricing at runtime rather than hard-coding it. ([GitHub][7])

So you could build your Copilot adapter around something like:

```ts
interface AgentModel {
  id: string;
  displayName: string;
  reasoningEfforts?: string[];
  supportsVision?: boolean;
  contextWindow?: number;
  policy?: unknown;
  billing?: unknown;
}

async function getAvailableCopilotModels(): Promise<AgentModel[]> {
  const client = new CopilotClient();
  await client.start();

  try {
    const models = await client.listModels();

    return models.map(m => ({
      id: m.id,
      displayName: m.name,
      reasoningEfforts: m.supportedReasoningEfforts,
      supportsVision: m.capabilities?.supports?.vision,
      contextWindow:
        m.capabilities?.limits?.max_context_window_tokens,
      policy: m.policy,
      billing: m.billing,
    }));
  } finally {
    await client.stop();
  }
}
```

For **agent-handoff**, I would use `listModels()` as the source of truth for Copilot rather than scraping `/model`, parsing documentation, or maintaining a hard-coded model list. It solves exactly the problem you were asking about earlier: model selection can follow whatever the currently authenticated user's Copilot subscription actually permits. ([GitHub Docs][1])

[1]: https://github.github.com/copilot-sdk-java/1.0.9/apidocs/allclasses-index.html?utm_source=chatgpt.com "All Classes and Interfaces (GitHub Copilot SDK for Java — Documentation Site 1.0.9 API)"
[2]: https://docs.github.com/en/copilot/concepts/models/overview?utm_source=chatgpt.com "Models in GitHub Copilot - GitHub Docs"
[3]: https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference?utm_source=chatgpt.com "GitHub Copilot CLI command reference - GitHub Docs"
[4]: https://github.com/headroomlabs-ai/headroom/issues/1039?utm_source=chatgpt.com "[BUG] GitHub Copilot models exposed by api.githubcopilot.com are filtered by Headroom proxy · Issue #1039 · headroomlabs-ai/headroom · GitHub"
[5]: https://github.com/github/copilot-cli/blob/main/changelog.md?utm_source=chatgpt.com "copilot-cli/changelog.md at main · github/copilot-cli · GitHub"
[6]: https://github.com/github/copilot-cli/issues/4527?utm_source=chatgpt.com "`copilot -p` fails with 401 on GHEC data residency since 1.0.81-1 — prompt mode model-catalog fetch hits `api.githubcopilot.com` instead of the tenant endpoint · Issue #4527 · github/copilot-cli · GitHub"
[7]: https://github.com/github/copilot-sdk/blob/main/docs/features/usage-and-billing.md?utm_source=chatgpt.com "copilot-sdk/docs/features/usage-and-billing.md at main · github/copilot-sdk · GitHub"
