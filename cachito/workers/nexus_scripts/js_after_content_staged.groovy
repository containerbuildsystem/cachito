/*
This script configures Nexus so that the NPM proxy repository for the Cachito request is blocked from getting additional
content.

In addition, a temporary user for the request is created and is given permission to access this NPM proxy repository.
It'd be preferable to give access to the Nexus anonymous user instead, but there is no way to add a role to a user.
You can only set the entire set of roles at once. This is an issue since if more than one Cachito request is in progress
and modifies the set of roles at the same time, one of the additions will be lost.
 */
import com.google.common.collect.Sets
import groovy.json.JsonSlurper
import groovy.transform.Field
import org.slf4j.Logger
import org.slf4j.LoggerFactory
import org.sonatype.nexus.security.role.NoSuchRoleException
import org.sonatype.nexus.security.user.UserStatus
import org.sonatype.nexus.repository.config.Configuration
import org.sonatype.nexus.security.authz.AuthorizationManager
import org.sonatype.nexus.security.role.Role
import static org.sonatype.nexus.security.user.UserManager.DEFAULT_SOURCE
import org.sonatype.nexus.security.user.UserNotFoundException


// Scope logger to the script using @Field
@Field final Logger logger = LoggerFactory.getLogger('cachito');


void createUser(String username, String password, List<String> roles) {
    try {
        // security is an object that is injected by Nexus when the script is executed
        def user = security.securitySystem.getUser(username)
        logger.info("Modifying the existing user ${username}")
        user.setFirstName(username)
        user.setLastName(username)
        user.setEmailAddress('noreply@domain.local')
        user.setStatus(UserStatus.active)
        security.securitySystem.updateUser(user)
        security.setUserRoles(username, roles)
        security.securitySystem.changePassword(username, password)
    } catch (UserNotFoundException e) {
        logger.info("Creating the user ${username}")
        String firstName = username
        String lastName = username
        String email = 'noreply@domain.local'
        Boolean active = true
        // security is an object that is injected by Nexus when the script is executed
        security.addUser(username, firstName, lastName, email, active, password, roles)
    }
}


void createRole(String name, String description, List<String> privileges) {
    // security is an object that is injected by Nexus when the script is executed
    AuthorizationManager authorizationManager = security.securitySystem.getAuthorizationManager(DEFAULT_SOURCE)

    String roleID = name
    try {
        Role role = authorizationManager.getRole(roleID)
        logger.info("Modifying the role ${name}")
        role.privileges = Sets.newHashSet(privileges)
        authorizationManager.updateRole(role)
    } catch (NoSuchRoleException e) {
        logger.info("Creating the role ${name}")
        List<String> roles = []
        security.addRole(roleID, name, description, privileges, roles)
    }
}


void blockOutboundConnections(String repositoryName) {
    logger.info("Blocking outbound connections from the NPM proxy repository ${repositoryName}")
    // repository is an object that is injected by Nexus when the script is executed
    Configuration repoConfig = repository.repositoryManager.get(repositoryName).configuration
    repoConfig.attributes('httpclient').set('blocked', true)
    repository.repositoryManager.update(repoConfig)
}


// Main execution starts here
request = new JsonSlurper().parseText(args)
['repository_name', 'password', 'username'].each { param ->
    assert request.get(param): "The ${param} parameter is required"
}

// Block outbound connections so that the proxy cannot fetch additional content
blockOutboundConnections(request.repository_name)
// Just name the role the same as the username for convenience
String roleName = request.username
// toString is needed to convert the GString to the Java String
List<String> privileges = ["nx-repository-view-npm-${request.repository_name}-read".toString()]
// Create a role that has read access on the new repository. This will allow a user with this role to utilize the
// the NPM proxy for this Cachito request.
createRole(roleName, "Read access for ${request.repository_name}".toString(), privileges)
List<String> roles = [roleName]
// Create a user with the role above
createUser(request.username, request.password, roles)

return 'The repository was configured successfully'
