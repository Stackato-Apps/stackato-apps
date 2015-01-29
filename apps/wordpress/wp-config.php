<?php
define('FS_METHOD', 'direct');

$url_parts = parse_url($_SERVER['DATABASE_URL']);
$db_name = substr( $url_parts{'path'}, 1 );

// ** MySQL settings from resource descriptor ** //
define('DB_NAME', $db_name);
define('DB_USER', $url_parts{'user'});
define('DB_PASSWORD', $url_parts{'pass'});
define('DB_HOST', $url_parts{'host'});
define('DB_PORT', $url_parts{'port'});

define('DB_CHARSET', 'utf8');
define('DB_COLLATE', '');
define ('WPLANG', '');
define('WP_DEBUG', false);

require('wp-salt.php');

$table_prefix  = 'wp_';

/* That's all, stop editing! Happy blogging. */

/** Absolute path to the WordPress directory. */
if ( !defined('ABSPATH') )
	define('ABSPATH', dirname(__FILE__) . '/');

/** Sets up WordPress vars and included files. */
require_once(ABSPATH . 'wp-settings.php');
?>
