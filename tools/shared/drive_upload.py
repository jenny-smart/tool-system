def upload_to_drive(service, folder_id, filename, media, file_metadata):
    existing = service.files().list(
        q=(
            f"name='{filename}' "
            f"and '{folder_id}' in parents "
            f"and trashed=false"
        ),
        fields="files(id,name)",
    ).execute()

    files = existing.get("files", [])

    if files:
        return service.files().update(
            fileId=files[0]["id"],
            media_body=media,
        ).execute()["id"]

    return service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id",
    ).execute()["id"]
