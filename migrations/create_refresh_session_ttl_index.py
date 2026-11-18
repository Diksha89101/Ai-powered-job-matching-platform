from migrations.context import app, mongo


if __name__ == '__main__':
    with app.app_context():
        index_name = mongo.db.refresh_sessions.create_index(
            'expires_at',
            expireAfterSeconds=0,
            name='refresh_sessions_expires_at_ttl',
        )
    print(f'Refresh-session expiration index ready: {index_name}')
