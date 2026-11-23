# Privacy Policy – derick-apps (The What If Lab autopost)

_Last updated: 23 November 2026_

**derick-apps** is a private, internal automation tool run by Puneeth N (individual creator, Andhra Pradesh, India) to publish videos to his own channels: the YouTube channel "The What If Lab", the Facebook Page "WHAT IF", the Instagram account @whatiflabs77 and his Dailymotion profile. It is not offered to the public and has no other users.

## What data the tool uses
- Video files, titles, descriptions, tags and thumbnails that the owner places in his own Google Drive folder.
- OAuth tokens for the owner's own accounts (YouTube scopes `youtube.upload` and `youtube.force-ssl`; Meta and Dailymotion equivalents). Tokens are stored only as encrypted GitHub Actions secrets.
- Upload results (video IDs, publish times) kept in a small state file in this repository.

## What it does with the data
- Uploads the owner's videos to the owner's channels, sets thumbnails, applies the altered/synthetic (AI) content label, and posts the owner's own first comment.
- It does **not** collect, read, store or share any data about other YouTube users or viewers.
- It does not sell data, show ads, or send data to any third party other than the platforms the videos are posted to.

## YouTube API Services
This tool uses YouTube API Services. By using it you agree to the [YouTube Terms of Service](https://www.youtube.com/t/terms). See the [Google Privacy Policy](https://policies.google.com/privacy). Access can be revoked at any time at [Google security settings](https://security.google.com/settings/security/permissions).

## Retention and deletion
Source files are moved to Drive Trash 30 days after posting. Tokens are deleted when access is revoked. The owner can delete all stored data by removing the repository secrets and state file.

## Contact
Puneeth N – puneethn561@gmail.com – Dasegowdanapalli, Agali, Anantapur, Andhra Pradesh 515311, India.
