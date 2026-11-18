import argparse
import logging
import os
import shutil
from pathlib import Path

from migrations.context import app, mongo
from app.security import random_upload_name, safe_upload_name


LOGGER = logging.getLogger(__name__)


def migrate_profile_images(
    remove_public_copies: bool = False,
) -> tuple[int, int, int]:
    source_folder = Path(app.root_path) / 'static' / 'profile-images'
    destination_folder = Path(app.config['PROFILE_IMAGE_FOLDER'])
    destination_folder.mkdir(parents=True, exist_ok=True)
    migrated = 0
    skipped = 0
    removed = 0

    for user in mongo.db.users.find(
        {'profile_image': {'$regex': r'^/static/profile-images/'}}
    ):
        old_url = user.get('profile_image', '')
        old_name = os.path.basename(old_url)
        safe_name = safe_upload_name(old_name, {'.jpg', '.jpeg', '.png', '.gif'})
        if not safe_name or safe_name != old_name:
            LOGGER.warning('Skipping invalid legacy profile image for user %s.', user['_id'])
            skipped += 1
            continue

        source = source_folder / safe_name
        if not source.is_file():
            LOGGER.warning('Legacy profile image is missing for user %s.', user['_id'])
            skipped += 1
            continue

        new_name = random_upload_name(safe_name)
        destination = destination_folder / new_name
        shutil.copy2(source, destination)
        new_url = (
            f"/api/users/{user['_id']}/profile-image/{new_name}"
        )
        result = mongo.db.users.update_one(
            {'_id': user['_id'], 'profile_image': old_url},
            {'$set': {'profile_image': new_url}},
        )
        if result.modified_count == 1:
            migrated += 1
        else:
            destination.unlink(missing_ok=True)
            skipped += 1

    if remove_public_copies:
        legacy_references = mongo.db.users.count_documents(
            {'profile_image': {'$regex': r'^/static/profile-images/'}}
        )
        if legacy_references:
            raise RuntimeError(
                f'{legacy_references} user records still reference public profile images.'
            )

        backup_folder = destination_folder / 'legacy-public-backup'
        backup_folder.mkdir(parents=True, exist_ok=True)
        for source in source_folder.iterdir():
            if source.name == '.gitkeep' or source.is_symlink() or not source.is_file():
                continue
            backup = backup_folder / source.name
            if not backup.exists():
                shutil.copy2(source, backup)
            source.unlink()
            removed += 1

    return migrated, skipped, removed


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--remove-public-copies',
        action='store_true',
        help='Remove legacy static images after copying them into private backups.',
    )
    arguments = parser.parse_args()
    with app.app_context():
        migrated_count, skipped_count, removed_count = migrate_profile_images(
            remove_public_copies=arguments.remove_public_copies
        )
    print(
        f'Private profile images migrated: {migrated_count}; '
        f'skipped: {skipped_count}; public copies removed: {removed_count}'
    )
