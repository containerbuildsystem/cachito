/*
This script configures Nexus for the Cachito development environment in an idempotent manner.

As part of the script, the following occurs:
- Anonymous access is disabled
- The "cachito-js" blob store is created
- The "cachito-pip" blob store is created
- The "cachito-js-hosted" NPM hosted repository is created
- The "cachito-js-proxy" NPM proxy repository is created
- The "cachito-pip-raw" raw hosted repository is created
- The "cachito-pip-proxy" PyPI proxy repository is created
- The "cachito-js" NPM group repository is created which points to the "cachito-js-hosted" and "cachito-js-proxy" repositories
- The "cachito" service account is created, which is used to manage the per-request proxy repositories, roles, and users
- The "cachito_unprivileged" service account is created, which is used by the per-request proxy repositories to connect to the "cachito-js" repository
 */
import com.google.common.collect.Sets
import groovy.json.JsonSlurper
import groovy.transform.Field
import org.slf4j.Logger
import org.slf4j.LoggerFactory
import org.sonatype.nexus.repository.config.Configuration
import org.sonatype.nexus.repository.storage.WritePolicy
import org.sonatype.nexus.security.authz.AuthorizationManager
import org.sonatype.nexus.security.role.NoSuchRoleException
import org.sonatype.nexus.security.role.Role
import org.sonatype.nexus.security.user.UserNotFoundException
import org.sonatype.nexus.security.user.UserStatus

import static org.sonatype.nexus.security.user.UserManager.DEFAULT_SOURCE


// Scope logger to the script using @Field
@Field final Logger logger = LoggerFactory.getLogger('cachito');


def createUser(String username, String password, List<String> roles) {
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


def createRole(String name, String description, List<String> privileges) {
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



def createBlobStore(String name) {
    if (!blobStore.blobStoreManager.get(name)) {
        logger.info("Creating the blob store ${name}")
        // Name the directory the same as the 'name' for where the blob store will be located.
        // If an absolute path is not provided such as in this case, it will be located at $NEXUS_DATA/<blobStorePath>.
        String blobStorePath = name
        blobStore.createFileBlobStore(name, blobStorePath)
    }
}


def createHostedRepo(String name, String repoType, String blobStoreName) {
    WritePolicy writePolicy = WritePolicy.ALLOW_ONCE
    Boolean strictContentValidation = true
    // repository is an object that is injected by Nexus when the script is executed
    if(repository.repositoryManager.exists(name)) {
        logger.info("Modifying the hosted repository ${name}")
        Configuration hostedRepoConfig = repository.repositoryManager.get(name).configuration
        def storage = hostedRepoConfig.attributes('storage')
        storage.set('strictContentTypeValidation', strictContentValidation)
        storage.set('writePolicy', writePolicy)
        repository.repositoryManager.update(hostedRepoConfig)
    }
    else {
        logger.info("Creating the hosted ${repoType} repository ${name}")
        switch(repoType) {
            case "raw":
                repository.createRawHosted(name, blobStoreName, strictContentValidation, writePolicy)
                break;
            case "npm":
                repository.createNpmHosted(name, blobStoreName, strictContentValidation, writePolicy)
                break;
            default:
                logger.warn("Type ${repoType} not supported. repository ${name} not created.")
                break;
        }
    }
}


def createProxyRepo(String name, String proxyType, String registry, String blobStoreName) {
    /*
        Here are some of the properties that can be set:
          repositoryName: name,
          recipeName: recipeName,
          online: true,
          attributes: [
            httpclient: [
              authentication: <See Below>,
              autoBlock: <Boolean>,
              blocked: <Boolean>,
              connection: https://bit.ly/39hLQBZ
            ],
            proxy: [
              remoteUrl: <String>,
              contentMaxAge: <Integer>,
              metadataMaxAge: <Integer>
            ],
            negativeCache: [
              enabled: <Boolean>,
              timeToLive: <Integer>
            ],
            storage: [
              blobStoreName: <String>,
              strictContentTypeValidation: <Boolean>
            ]
          ]

        httpclient.authentication (select from one of the following types):
        - username: https://bit.ly/2WMAxPB
        - ntlm: https://bit.ly/3bC87w7
        - bearerToken: https://bit.ly/2QL4egm

        For example:
          def authentication = repo_config.attributes('httpclient').child('authentication')
          authentication.set('type', 'username')
          authentication.set('username', 'cachito')
          authentication.set('password', 'secret_password')
     */
    Configuration proxyRepoConfig
    // repository is an object that is injected by Nexus when the script is executed
    Boolean repoExists = repository.repositoryManager.exists(name)
    if(repoExists) {
        logger.info("Modifying the proxy repository ${name}")
        proxyRepoConfig = repository.repositoryManager.get(name).configuration
    }
    else {
        logger.info("Creating the proxy repository ${name}")
        proxyRepoConfig = repository.repositoryManager.newConfiguration()
        proxyRepoConfig.repositoryName = name
        proxyRepoConfig.recipeName = proxyType
        proxyRepoConfig.attributes('storage').set('blobStoreName', blobStoreName)
    }

    proxyRepoConfig.online = true
    proxyRepoConfig.attributes('storage').set('strictContentTypeValidation', true)

    def proxy = proxyRepoConfig.attributes('proxy')
    proxy.set('remoteUrl', registry)
    // Don't expire the cache
    proxy.set('contentMaxAge', -1)
    proxy.set('metadataMaxAge', -1)

    def httpclient = proxyRepoConfig.attributes('httpclient')
    httpclient.set('autoBlock', true)
    httpclient.set('blocked', false)

    proxyRepoConfig.attributes('negativeCache').set('enabled', false)

    if(repoExists) {
        repository.repositoryManager.update(proxyRepoConfig)
    }
    else {
        repository.repositoryManager.create(proxyRepoConfig)
    }
}


def createGroupNpmRepo(String name, List<String> groupMembers, String blobStoreName) {
    // repository is an object that is injected by Nexus when the script is executed
    if(repository.repositoryManager.exists(name)) {
        logger.info("Modifying the group repository ${name}")
        Configuration groupRepoConfig = repository.repositoryManager.get(name).configuration
        groupRepoConfig.attributes('group').set('memberNames', groupMembers)
        repository.repositoryManager.update(groupRepoConfig)
    } else {
        logger.info("Creating the group repository ${name}")
        repository.createNpmGroup(name, groupMembers, blobStoreName)
    }
}


def createCachitoUser(String password) {
    String description = 'Admin access on all the repositories'
    List<String> privileges = [
            // This is required so that Cachito can reset a created user's password if a Groovy script is executed again
            // due to a worker crashing and rerunning the task. This is necessary because Cachito will generate the
            // password as part of the task and not store it in the database.
            'nx-userschangepw',
            // This is so that Cachito can create, read, update, and delete Nexus repositories
            'nx-repository-admin-*-*-*',
            // This is so that Cachito can use any NPM repository
            'nx-repository-view-npm-*-*',
            // This is so that Cachito can use any PyPI repository
            'nx-repository-view-pypi-*-*',
            // This is so that Cachito can use any raw repository
            'nx-repository-view-raw-*-*',
            // This is so that Cachito can create, read, update, and delete Nexus roles
            'nx-roles-all',
            // This is so that Cachito can create, read, update, and delete Nexus Groovy scripts
            'nx-script-*-*',
            // This is so that Cachito can create, read, update, and delete Nexus users
            'nx-users-all',
    ]
    createRole('cachito', description, privileges)

    createUser('cachito', password, ['cachito'])
}


def createCachitoUnprivilegedUser(String password) {
    // This creates an unprivileged user that just has the ability to use the cachito-js Nexus repository. This is
    // the account used for authentication against the cachito-js Nexus repository for the NPM proxy repositories
    // created per Cachito request.
    List<String> privileges = [
        // This allows the unprivileged user to use the cachito-js Nexus repository
        'nx-repository-view-npm-cachito-js-*'
    ]
    String description = 'The user that can just use the main cachito repositories'
    createRole('cachito_unprivileged', description, privileges)

    createUser('cachito_unprivileged', password, ['cachito_unprivileged'])
}


// Main execution starts here
def request = new JsonSlurper().parseText(args)
['base_url', 'cachito_password', 'cachito_unprivileged_password'].each { param ->
    assert request.get(param): "The ${param} parameter is required"
    assert request.get(param) instanceof String: "The ${param} parameter must be a string"
}
if (request.new_admin_password) {
    assert request.new_admin_password instanceof String: 'The new_admin_password parameter must be a string'
}

// Set the baseURL to prevent exceptions in the remaining code
// core is an object that is injected by Nexus when the script is executed
logger.info("Setting the base URL to ${request.base_url}")
core.baseUrl(request.base_url)

// Set the admin user's password if requested
if (request.new_admin_password) {
    logger.info("Setting the admin password")
    // security is an object that is injected by Nexus when the script is executed
    security.securitySystem.changePassword('admin', request.new_admin_password)
}

logger.info("Disabling anonymous access")
// security is an object that is injected by Nexus when the script is executed
security.setAnonymousAccess(false)

String jsBlobStoreName = 'cachito-js'
createBlobStore(jsBlobStoreName)

String jsHostedRepoName = 'cachito-js-hosted'
String jsHostedType = 'npm'
createHostedRepo(jsHostedRepoName, jsHostedType, jsBlobStoreName)

String jsProxyRepoName = 'cachito-js-proxy'
String jsRegistry = 'https://registry.npmjs.org'
String jsProxyType = 'npm-proxy'
createProxyRepo(jsProxyRepoName, jsProxyType, jsRegistry, jsBlobStoreName)

String jsGroupRepoName = 'cachito-js'
List<String> jsGroupMembers = [jsHostedRepoName, jsProxyRepoName]
createGroupNpmRepo(jsGroupRepoName, jsGroupMembers, jsBlobStoreName)

String pipBlobStoreName = 'cachito-pip'
createBlobStore(pipBlobStoreName)

String pipHostedRepoName = 'cachito-pip-raw'
String pipHostedType = 'raw'
createHostedRepo(pipHostedRepoName, pipHostedType, pipBlobStoreName)

String pipProxyRepoName = 'cachito-pip-proxy'
String pipRegistry = 'https://pypi.org/'
String pipProxyType = 'pypi-proxy'
createProxyRepo(pipProxyRepoName, pipProxyType, pipRegistry, pipBlobStoreName)


createCachitoUser(request.cachito_password)
createCachitoUnprivilegedUser(request.cachito_unprivileged_password)

return "Nexus was configured successfully"
