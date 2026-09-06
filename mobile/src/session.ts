/**
 * The app's single Session instance.
 *
 * One module so the websocket, both API clients and the sign-in screen share
 * the same token state - and, critically, the same single-flight refresh.
 */

import {Session} from './auth/session';
import {config} from './config';

export const session = new Session({baseUrl: config.apiBaseUrl});
