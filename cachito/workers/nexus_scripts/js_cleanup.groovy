/*
This script deletes the NPM proxy repository, user, and role for the Cachito request.

This script should get executed when the Cachito request is set to the stale state.
 */
import groovy.json.JsonSlurper
import org.slf4j.Logger
import org.slf4j.LoggerFactory
import org.sonatype.nexus.security.authz.AuthorizationManager
import org.sonatype.nexus.security.role.NoSuchRoleException
import org.sonatype.nexus.security.user.UserNotFoundException

import static org.sonatype.nexus.security.user.UserManager.DEFAULT_SOURCE


request = new JsonSlurper().parseText(args)
['repository_name', 'username'].each { param ->
    assert request.get(param): "The ${param} parameter is required"
}

final Logger logger = LoggerFactory.getLogger('cachito');
try {
    logger.info("Deleting the user ${request.username}")
    // security is an object that is injected by Nexus when the script is executed
    security.securitySystem.deleteUser(request.username, DEFAULT_SOURCE)
} catch(UserNotFoundException e) {
    logger.warn("The user ${request.username} was not found")
}

// security is an object that is injected by Nexus when the script is executed
AuthorizationManager authorizationManager = security.securitySystem.getAuthorizationManager(DEFAULT_SOURCE)
// The role is named the same as the username
String roleName = request.username
try {
    logger.info("Deleting the role ${roleName}")
    authorizationManager.deleteRole(roleName)
} catch(NoSuchRoleException e) {
    logger.warn("The role ${roleName} was not found")
}

// repository is an object that is injected by Nexus when the script is executed
logger.info("Deleting the repository ${request.repository_name}")
if (repository.repositoryManager.exists(request.repository_name)) {
    repository.repositoryManager.delete(request.repository_name)
} else {
    logger.warn("The repository ${request.repository_name} was not found")
}

return "The NPM proxy repository ${request.repository_name} and the user and role ${request.username} are removed"
