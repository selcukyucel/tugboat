# Estate map

`estate.json` lists the project's apps, their modules, who owns each, and the environments they run in.
Given a file path in an app repo, the agent uses this file to name the owner.

Each app has:

| Field | Meaning |
|---|---|
| `id`, `name` | A short ID and a readable name |
| `platform` | `ios` or `android` |
| `repo` | The app repo, as `owner/name` |
| `owner` | The person or group who owns the app as a whole |
| `environments` | Such as `dev`, `staging`, `production` |
| `modules` | Parts of the app. Each has an `id`, an `owner`, and the `paths` it covers. |

A path that matches no module belongs to the app's owner.

The backend is not described here.

```json
{
  "schema": "tug.estate/1",
  "apps": [
    {
      "id": "shop-ios",
      "name": "Shop for iOS",
      "platform": "ios",
      "repo": "your-org/shop-ios",
      "owner": "ios-team",
      "environments": ["dev", "staging", "production"],
      "modules": [
        { "id": "checkout", "owner": "a-github-handle", "paths": ["Sources/Checkout/**"] }
      ]
    }
  ]
}
```
