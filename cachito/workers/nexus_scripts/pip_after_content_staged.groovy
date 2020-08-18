/*
This script configures Nexus so that a temporary user for the request is created and is given
permission to access the PyPI hosted repository and the raw hosted repository. It'd be preferable
to give access to the Nexus anonymous user instead, but there is no way to add a role to a user.
You can only set the entire set of roles at once. This is an issue since if more than one Cachito
request is in progress and modifies the set of roles at the same time, one of the additions will be
lost.

Differently from its JS counterpart, this script does not block outbound connections for the
temporary request repositories. This is not needed because both Python repositories used here
are hosted repositories, and not PyPI proxies. In other words, they would not automatically pull
unavailable contents from PyPI, as the npm repository would.
 */
import com.google.common.collect.Sets
import groovy.json.JsonSlurper
import groovy.transform.Field
import org.slf4j.Logger
import org.slf4j.LoggerFactory
import org.sonatype.nexus.security.role.NoSuchRoleException
import org.sonatype.nexus.security.user.UserStatus
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


// Main execution starts here
request = new JsonSlurper().parseText(args)
['pip_repository_name', 'raw_repository_name', 'password', 'username'].each { param ->
    assert request.get(param): "The ${param} parameter is required"
}

// Just name the role the same as the username for convenience
String roleName = request.username
// toString is needed to convert the GString to the Java String
String pypiHostedPrivilege = "nx-repository-view-pypi-${request.pip_repository_name}-read".toString()
String rawHostedPrivilege = "nx-repository-view-raw-${request.raw_repository_name}-read".toString()
List<String> privileges = [pypiHostedPrivilege, rawHostedPrivilege]
// Create a role that has read access on the new repositories.
// This will allow a user with this role to utilize the the Python repos for this Cachito request.
String desc = "Read access for ${request.pip_repository_name} and ${request.raw_repository_name}".toString()
createRole(roleName, desc, privileges)
List<String> roles = [roleName]
// Create a user with the role above
createUser(request.username, request.password, roles)

return 'The repositories, user, and role were configured successfully'
