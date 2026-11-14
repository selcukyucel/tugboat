# Estate map

`estate.json` is the project's inventory. It lists the apps, the repo each one lives in, the modules of each app, who owns each, and the environments an app runs in.

It answers one question, for people, for the agent, and for reports: who owns this file? In an app repo:

```sh
tug owner Sources/Checkout/Cart.swift
```

The starter file lists no app. Add yours with a pull request.

Each app has:

| Field | Meaning |
|---|---|
| `id`, `name` | A short ID and a readable name |
| `platform` | `ios` or `android` |
| `repo` | The app repo, as `owner/name` |
| `owner` | The person or group who owns the app as a whole |
| `environments` | Such as `dev`, `staging`, `production` |
| `modules` | Parts of the app. Each has an `id`, an `owner`, and the `paths` it covers. |

How an owner is found:

- A module names the paths it covers. When several modules cover a path, the narrowest path wins: `Sources/Checkout/Payments/**` beats `Sources/Checkout/**`.
- A path that no module covers belongs to the app's owner.
- An owner is a person or a group, written the way the team writes it, such as a GitHub handle.

In a path, `*` matches inside one folder or file name, `?` matches one character, and `**` matches any number of folders. A path covers what it matches and everything beneath it.

`repo` must be an app repo of the project profile. `tug team check` warns when it is not, because a wrong name means no owner is found in that repo.

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
